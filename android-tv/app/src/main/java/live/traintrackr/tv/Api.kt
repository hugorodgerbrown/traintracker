package live.traintrackr.tv

import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/** Talks to the server's pairing endpoints. Blocking: call it off the main thread. */
class Api(private val server: String) {

    /** Thrown when the server asks us to slow down. */
    class RateLimited : IOException("Too many pairing requests")

    /** Start a pairing and return its code. */
    fun start(): Protocol.Started {
        val (status, json) = request("$server/tv/pair", method = "POST")
        if (status == 429) throw RateLimited()
        if (status != 201 || json == null) throw IOException("Pairing failed to start: HTTP $status")
        return Protocol.parseStarted(json)
    }

    /** Ask whether the code has been typed. */
    fun poll(pollUrl: String): Protocol.Poll {
        if (!Protocol.isOwnPage(pollUrl, server)) return Protocol.Poll.Expired
        val (status, json) = request(pollUrl, method = "GET")
        if (status >= 500) throw IOException("Server error: HTTP $status")
        return Protocol.parsePoll(status, json, server)
    }

    private fun request(url: String, method: String): Pair<Int, JSONObject?> {
        val connection = URL(url).openConnection() as HttpURLConnection
        try {
            connection.requestMethod = method
            connection.connectTimeout = TIMEOUT_MS
            connection.readTimeout = TIMEOUT_MS
            connection.instanceFollowRedirects = false
            connection.setRequestProperty("Accept", "application/json")
            connection.setRequestProperty("User-Agent", "TraintrackrTV/${BuildConfig.VERSION_NAME}")
            if (method == "POST") {
                connection.doOutput = true
                connection.setFixedLengthStreamingMode(0)
                connection.outputStream.close()
            }
            val status = connection.responseCode
            val stream = if (status < 400) connection.inputStream else connection.errorStream
            val body = stream?.bufferedReader()?.use { it.readText() }
            val json = body?.let { runCatching { JSONObject(it) }.getOrNull() }
            return status to json
        } finally {
            connection.disconnect()
        }
    }

    private companion object {
        const val TIMEOUT_MS = 10_000
    }
}
