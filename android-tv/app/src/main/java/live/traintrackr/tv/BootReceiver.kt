package live.traintrackr.tv

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.provider.Settings
import android.util.Log

/**
 * Opens the board when the TV turns on, if the person asked for that.
 *
 * Android 10 and later only let an app start from the background once it
 * may draw over other apps, so without that permission this does nothing.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        val store = Store(context)
        if (!store.startOnBoot || store.boardUrl == null) return
        if (!Settings.canDrawOverlays(context)) {
            Log.i(TAG, "Start on boot is on, but the app may not draw over other apps")
            return
        }
        context.startActivity(
            Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        )
    }

    private companion object {
        const val TAG = "Traintrackr"
    }
}
