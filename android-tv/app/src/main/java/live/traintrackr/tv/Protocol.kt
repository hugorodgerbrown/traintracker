package live.traintrackr.tv

import org.json.JSONObject
import java.net.URI

/**
 * What the server says during pairing (apps/pairing/views.py), and which
 * board addresses the app will open. Pure functions, so they are unit tested
 * without a device.
 */
object Protocol {

    /** A started pairing: the code to show, the link for its QR code, and where to poll. */
    data class Started(
        val code: String,
        val pairUrl: String,
        val pollUrl: String,
        val pollSeconds: Int,
    )

    /** One answer to a poll. */
    sealed interface Poll {
        /** Nobody has typed the code yet. */
        data object Waiting : Poll

        /** The code was typed and a board chosen: show it from now on. */
        data class Paired(val boardUrl: String, val boardName: String) : Poll

        /** The code ran out or was already used: start again with a new one. */
        data object Expired : Poll
    }

    /** Read the answer to `POST /tv/pair`. */
    fun parseStarted(json: JSONObject): Started = Started(
        code = json.getString("code"),
        pairUrl = json.getString("pair_url"),
        pollUrl = json.getString("poll_url"),
        pollSeconds = json.optInt("poll_seconds", 3).coerceIn(1, 30),
    )

    /** Read the answer to `GET /tv/pair/<token>`, given its HTTP status. */
    fun parsePoll(status: Int, json: JSONObject?, server: String): Poll {
        if (status == 404 || status == 410 || json == null) return Poll.Expired
        return when (json.optString("status")) {
            "waiting" -> Poll.Waiting
            "paired" -> {
                val url = json.optString("board_url")
                if (isBoardUrl(url, server)) {
                    Poll.Paired(url, json.optString("board_name"))
                } else {
                    Poll.Expired
                }
            }
            else -> Poll.Expired
        }
    }

    // A board's one address (apps/public/board.py): a station's code, then
    // optionally a destination's code or a platform.
    private val BOARD_PATH = Regex("^/board/[A-Z]{3}(/[0-9A-Z]{1,4})?$")

    /** True for a board's TV view on our own server (`<server>/board/LST/COL?tv=1`), and nothing else. */
    fun isBoardUrl(url: String, server: String): Boolean {
        val uri = runCatching { URI(url) }.getOrNull() ?: return false
        return sameOrigin(uri, server) && uri.rawQuery == "tv=1" && BOARD_PATH.matches(uri.rawPath ?: "")
    }

    /** True when [url] is on our own server, so the board's WebView may follow it. */
    fun isOwnPage(url: String, server: String): Boolean {
        val uri = runCatching { URI(url) }.getOrNull() ?: return false
        return sameOrigin(uri, server)
    }

    /** The address people type on their phone: the /tv page without the scheme or code. */
    fun typedPairAddress(server: String): String =
        "${server.substringAfter("://")}/tv"

    private fun sameOrigin(uri: URI, server: String): Boolean {
        val own = URI(server)
        return uri.scheme.equals(own.scheme, ignoreCase = true) &&
            uri.host.equals(own.host, ignoreCase = true) &&
            effectivePort(uri) == effectivePort(own)
    }

    private fun effectivePort(uri: URI): Int = when {
        uri.port != -1 -> uri.port
        uri.scheme.equals("https", ignoreCase = true) -> 443
        else -> 80
    }
}
