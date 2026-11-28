package live.traintrackr.tv

import android.content.Context

/** The little the app remembers: which board it shows, and whether to start with the TV. */
class Store(context: Context) {
    private val prefs = context.getSharedPreferences("traintrackr-tv", Context.MODE_PRIVATE)

    /** The TV view of the board this TV shows, or null until a phone chooses one. */
    var boardUrl: String?
        get() = prefs.getString(KEY_BOARD_URL, null)
        set(value) = prefs.edit().putString(KEY_BOARD_URL, value).apply()

    /** The board's short name, for the menu ("London Liverpool Street to Colchester"). */
    var boardName: String?
        get() = prefs.getString(KEY_BOARD_NAME, null)
        set(value) = prefs.edit().putString(KEY_BOARD_NAME, value).apply()

    /** Open the board when the TV turns on. Off unless the person turns it on. */
    var startOnBoot: Boolean
        get() = prefs.getBoolean(KEY_START_ON_BOOT, false)
        set(value) = prefs.edit().putBoolean(KEY_START_ON_BOOT, value).apply()

    private companion object {
        const val KEY_BOARD_URL = "board_url"
        const val KEY_BOARD_NAME = "board_name"
        const val KEY_START_ON_BOOT = "start_on_boot"
    }
}
