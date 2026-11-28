package live.traintrackr.tv

import android.annotation.SuppressLint
import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.ConnectivityManager
import android.net.Network
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.KeyEvent
import android.view.View
import android.view.WindowInsets
import android.view.WindowManager
import android.webkit.RenderProcessGoneDetail
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.ImageView
import android.widget.TextView
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/**
 * The whole app: a code screen until a phone chooses a board, then that
 * board's TV view, full screen, for as long as the app is open.
 *
 * The board itself (flaps, sizing, clock, the minute's fetch, the nightly
 * reload) is the web page at `/board/<station>[/<then>]?tv=1`; this activity
 * only does what a TV's browser does badly: open from the home screen, stay
 * awake, recover by itself, and be given a board without typing an address
 * (docs/decisions/a-tv-picks-its-board-from-a-phone.md).
 */
class MainActivity : Activity() {

    private val server = BuildConfig.SERVER_URL
    private val api = Api(server)
    private val main = Handler(Looper.getMainLooper())
    private lateinit var io: ExecutorService
    private lateinit var store: Store

    private lateinit var board: WebView
    private lateinit var pairPanel: View
    private lateinit var pairAddress: TextView
    private lateinit var pairCode: View
    private lateinit var pairFlaps: List<TextView>
    private lateinit var pairQr: ImageView
    private lateinit var pairStatus: TextView
    private lateinit var reconnecting: View
    private lateinit var menu: View
    private lateinit var menuShowing: TextView
    private lateinit var menuStartOnBoot: Button
    private lateinit var menuHint: TextView

    // Bumped whenever the screen changes, so late answers from the network are ignored.
    private var generation = 0
    private var boardFailed = false
    private var retryDelayMs = FIRST_RETRY_MS

    private val retryBoard = Runnable { loadBoard() }

    private val networkCallback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) {
            main.post {
                if (boardFailed) {
                    main.removeCallbacks(retryBoard)
                    loadBoard()
                }
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        store = Store(this)

        board = findViewById(R.id.board)
        pairPanel = findViewById(R.id.pair)
        pairAddress = findViewById(R.id.pair_address)
        pairCode = findViewById(R.id.pair_code)
        pairFlaps = listOf(
            R.id.pair_flap_0, R.id.pair_flap_1, R.id.pair_flap_2,
            R.id.pair_flap_3, R.id.pair_flap_4, R.id.pair_flap_5,
        ).map { findViewById(it) }
        pairQr = findViewById(R.id.pair_qr)
        pairStatus = findViewById(R.id.pair_status)
        reconnecting = findViewById(R.id.reconnecting)
        menu = findViewById(R.id.menu)
        menuShowing = findViewById(R.id.menu_showing)
        menuStartOnBoot = findViewById(R.id.menu_start_on_boot)
        menuHint = findViewById(R.id.menu_hint)

        pairAddress.text = Protocol.typedPairAddress(server)
        setUpBoard()
        setUpMenu()
    }

    override fun onStart() {
        super.onStart()
        io = Executors.newSingleThreadExecutor()
        hideSystemBars()
        getSystemService(ConnectivityManager::class.java).registerDefaultNetworkCallback(networkCallback)
        board.onResume()
        val boardUrl = store.boardUrl
        if (boardUrl != null && Protocol.isBoardUrl(boardUrl, server)) showBoard() else startPairing()
    }

    override fun onStop() {
        generation++
        main.removeCallbacksAndMessages(null)
        getSystemService(ConnectivityManager::class.java).unregisterNetworkCallback(networkCallback)
        board.onPause()
        io.shutdownNow()
        super.onStop()
    }

    override fun onDestroy() {
        board.destroy()
        super.onDestroy()
    }

    // The remote's Back and Menu keys open the app's menu over the board;
    // everything else (arrows, OK, number keys) goes to the board page.
    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        val isBack = event.keyCode == KeyEvent.KEYCODE_BACK
        val isMenu = event.keyCode == KeyEvent.KEYCODE_MENU
        if (!isBack && !isMenu) return super.dispatchKeyEvent(event)
        if (event.action == KeyEvent.ACTION_UP) {
            when {
                menu.visibility == View.VISIBLE -> closeMenu()
                pairPanel.visibility != View.VISIBLE -> openMenu()
                isBack -> finish()
            }
        }
        return true
    }

    // --- Pairing -----------------------------------------------------------

    private fun startPairing() {
        val gen = ++generation
        // Stop recovering the board, or a late retry would restart pairing and replace this code.
        main.removeCallbacks(retryBoard)
        boardFailed = false
        board.visibility = View.GONE
        board.loadUrl("about:blank")
        reconnecting.visibility = View.GONE
        menu.visibility = View.GONE
        pairPanel.visibility = View.VISIBLE
        showCode(getString(R.string.pair_code_placeholder))
        pairQr.setImageDrawable(null)
        pairStatus.setText(R.string.pair_getting_code)
        io.execute {
            val result = runCatching { api.start() }
            main.post {
                if (gen != generation) return@post
                result.fold(
                    onSuccess = { started ->
                        showCode(started.code)
                        pairQr.setImageBitmap(qrBitmap(started.pairUrl, QR_PIXELS))
                        pairStatus.setText(R.string.pair_waiting)
                        schedulePoll(gen, started)
                    },
                    onFailure = { error ->
                        val limited = error is Api.RateLimited
                        pairStatus.setText(if (limited) R.string.pair_busy else R.string.pair_offline)
                        main.postDelayed(
                            { if (gen == generation) startPairing() },
                            if (limited) RATE_LIMITED_RETRY_MS else FIRST_RETRY_MS,
                        )
                    },
                )
            }
        }
    }

    private fun schedulePoll(gen: Int, started: Protocol.Started) {
        main.postDelayed({ if (gen == generation) poll(gen, started) }, started.pollSeconds * 1000L)
    }

    private fun poll(gen: Int, started: Protocol.Started) {
        io.execute {
            val result = runCatching { api.poll(started.pollUrl) }
            main.post {
                if (gen != generation) return@post
                result.fold(
                    onSuccess = { answer ->
                        when (answer) {
                            Protocol.Poll.Waiting -> {
                                pairStatus.setText(R.string.pair_waiting)
                                schedulePoll(gen, started)
                            }
                            is Protocol.Poll.Paired -> {
                                store.boardUrl = answer.boardUrl
                                store.boardName = answer.boardName
                                showBoard()
                            }
                            Protocol.Poll.Expired -> startPairing()
                        }
                    },
                    onFailure = {
                        pairStatus.setText(R.string.pair_offline)
                        schedulePoll(gen, started)
                    },
                )
            }
        }
    }

    /** Draw [code] on the six flaps (its dash is the gap), and give TalkBack the whole code. */
    private fun showCode(code: String) {
        val letters = code.filter { it != '-' }
        pairFlaps.forEachIndexed { i, flap -> flap.text = letters.getOrNull(i)?.toString() ?: "" }
        pairCode.contentDescription = code
    }

    // --- The board ---------------------------------------------------------

    @SuppressLint("SetJavaScriptEnabled")
    private fun setUpBoard() {
        board.setBackgroundColor(getColor(R.color.green))
        board.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = false
            allowContentAccess = false
        }
        board.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean =
                !Protocol.isOwnPage(request.url.toString(), server)

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (request.isForMainFrame) boardLoadFailed()
            }

            override fun onReceivedHttpError(
                view: WebView,
                request: WebResourceRequest,
                response: WebResourceResponse,
            ) {
                if (!request.isForMainFrame) return
                // A board that no longer exists asks for another; a busy or failing server is retried.
                when {
                    response.statusCode == 404 -> chooseAnotherBoard()
                    response.statusCode == 429 || response.statusCode >= 500 -> boardLoadFailed()
                }
            }

            override fun onPageFinished(view: WebView, url: String) {
                if (boardFailed || url == "about:blank") return
                retryDelayMs = FIRST_RETRY_MS
                reconnecting.visibility = View.GONE
                board.visibility = View.VISIBLE
            }

            // A crashed renderer takes the WebView with it: start again cleanly.
            override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
                recreate()
                return true
            }
        }
    }

    private fun showBoard() {
        generation++
        pairPanel.visibility = View.GONE
        loadBoard()
    }

    private fun loadBoard() {
        val url = store.boardUrl ?: return startPairing()
        boardFailed = false
        board.loadUrl(url)
        board.requestFocus()
    }

    private fun boardLoadFailed() {
        boardFailed = true
        board.visibility = View.INVISIBLE
        reconnecting.visibility = View.VISIBLE
        main.removeCallbacks(retryBoard)
        main.postDelayed(retryBoard, retryDelayMs)
        retryDelayMs = (retryDelayMs * 2).coerceAtMost(MAX_RETRY_MS)
    }

    // --- The menu ----------------------------------------------------------

    private fun setUpMenu() {
        findViewById<Button>(R.id.menu_keep_watching).setOnClickListener { closeMenu() }
        findViewById<Button>(R.id.menu_choose_board).setOnClickListener { chooseAnotherBoard() }
        findViewById<Button>(R.id.menu_exit).setOnClickListener { finish() }
        menuStartOnBoot.setOnClickListener { toggleStartOnBoot() }
    }

    private fun openMenu() {
        menuStartOnBoot.setText(if (store.startOnBoot) R.string.menu_start_on_boot_on else R.string.menu_start_on_boot_off)
        val name = store.boardName
        menuShowing.text = if (name.isNullOrEmpty()) "" else getString(R.string.menu_showing, name)
        menuShowing.visibility = if (name.isNullOrEmpty()) View.GONE else View.VISIBLE
        menuHint.visibility = View.GONE
        menu.visibility = View.VISIBLE
        findViewById<View>(R.id.menu_keep_watching).requestFocus()
    }

    private fun closeMenu() {
        menu.visibility = View.GONE
        board.requestFocus()
    }

    private fun chooseAnotherBoard() {
        store.boardUrl = null
        store.boardName = null
        startPairing()
    }

    private fun toggleStartOnBoot() {
        store.startOnBoot = !store.startOnBoot
        menuStartOnBoot.setText(if (store.startOnBoot) R.string.menu_start_on_boot_on else R.string.menu_start_on_boot_off)
        if (!store.startOnBoot || Settings.canDrawOverlays(this)) {
            menuHint.visibility = View.GONE
            return
        }
        // Starting with the TV needs "display over other apps"; Google TV
        // often hides that setting, so say how to allow it if it isn't there.
        try {
            startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))
        } catch (e: ActivityNotFoundException) {
            menuHint.text = getString(R.string.menu_start_on_boot_hint, packageName)
            menuHint.visibility = View.VISIBLE
        }
    }

    @Suppress("DEPRECATION")
    private fun hideSystemBars() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            window.insetsController?.hide(WindowInsets.Type.systemBars())
        } else {
            window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_FULLSCREEN or
                View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
        }
    }

    private companion object {
        const val QR_PIXELS = 480
        const val FIRST_RETRY_MS = 5_000L
        const val MAX_RETRY_MS = 60_000L
        const val RATE_LIMITED_RETRY_MS = 60_000L
    }
}
