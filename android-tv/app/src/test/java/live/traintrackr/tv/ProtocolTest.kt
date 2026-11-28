package live.traintrackr.tv

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ProtocolTest {
    private val server = "https://traintrackr.example"
    private val board = "$server/board/LST/COL?tv=1"

    @Test
    fun readsAStartedPairing() {
        val started = Protocol.parseStarted(
            JSONObject(
                """{"code": "K7Q-4MD", "pair_url": "$server/tv?code=K7Q4MD",
                   "poll_url": "$server/tv/pair/abc", "poll_seconds": 3, "expires_at": "x"}""",
            ),
        )
        assertEquals("K7Q-4MD", started.code)
        assertEquals("$server/tv?code=K7Q4MD", started.pairUrl)
        assertEquals("$server/tv/pair/abc", started.pollUrl)
        assertEquals(3, started.pollSeconds)
    }

    @Test
    fun keepsThePollIntervalSensible() {
        val json = JSONObject("""{"code": "A", "pair_url": "b", "poll_url": "c", "poll_seconds": 0}""")
        assertEquals(1, Protocol.parseStarted(json).pollSeconds)
    }

    @Test
    fun readsEachPollAnswer() {
        assertEquals(Protocol.Poll.Waiting, Protocol.parsePoll(200, JSONObject("""{"status": "waiting"}"""), server))
        assertEquals(
            Protocol.Poll.Paired(board, "London Liverpool Street to Colchester"),
            Protocol.parsePoll(
                200,
                JSONObject(
                    """{"status": "paired", "board_url": "$board",
                       "board_name": "London Liverpool Street to Colchester"}""",
                ),
                server,
            ),
        )
        assertEquals(Protocol.Poll.Expired, Protocol.parsePoll(410, JSONObject("""{"status": "expired"}"""), server))
        assertEquals(Protocol.Poll.Expired, Protocol.parsePoll(404, null, server))
        assertEquals(Protocol.Poll.Expired, Protocol.parsePoll(200, JSONObject("{}"), server))
    }

    @Test
    fun refusesALinkToAnotherSite() {
        val elsewhere = JSONObject("""{"status": "paired", "board_url": "https://evil.example/board/LST?tv=1"}""")
        assertEquals(Protocol.Poll.Expired, Protocol.parsePoll(200, elsewhere, server))
    }

    @Test
    fun onlyOpensABoardsTvView() {
        assertTrue(Protocol.isBoardUrl(board, server))
        assertTrue(Protocol.isBoardUrl("$server/board/LST?tv=1", server))
        assertTrue(Protocol.isBoardUrl("$server/board/CBG/10A?tv=1", server))
        assertFalse(Protocol.isBoardUrl("http://traintrackr.example/board/LST?tv=1", server))
        assertFalse(Protocol.isBoardUrl("$server/board/LST", server))
        assertFalse(Protocol.isBoardUrl("$server/board/LST?tv=1&embed=1", server))
        assertFalse(Protocol.isBoardUrl("$server/board/lst?tv=1", server))
        assertFalse(Protocol.isBoardUrl("$server/account?tv=1", server))
        assertFalse(Protocol.isBoardUrl("$server/board/LST/COL/X?tv=1", server))
        assertFalse(Protocol.isBoardUrl("not a url", server))
    }

    @Test
    fun followsOnlyOwnPages() {
        assertTrue(Protocol.isOwnPage("$server/tv/pair/abc", server))
        assertTrue(Protocol.isOwnPage("https://TRAINTRACKR.example:443/", server))
        assertFalse(Protocol.isOwnPage("https://traintrackr.example.evil/", server))
        assertFalse(Protocol.isOwnPage("javascript:alert(1)", server))
    }

    @Test
    fun showsTheAddressToType() {
        assertEquals("traintrackr.example/tv", Protocol.typedPairAddress(server))
    }
}
