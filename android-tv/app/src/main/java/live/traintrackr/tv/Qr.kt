package live.traintrackr.tv

import android.graphics.Bitmap
import android.graphics.Color
import com.google.zxing.BarcodeFormat
import com.google.zxing.EncodeHintType
import com.google.zxing.qrcode.QRCodeWriter

/** Draws a QR code for [text], the board's well on white, so any phone's camera reads it, [size] pixels square. */
fun qrBitmap(text: String, size: Int): Bitmap {
    val matrix = QRCodeWriter().encode(
        text,
        BarcodeFormat.QR_CODE,
        size,
        size,
        mapOf(EncodeHintType.MARGIN to 1),
    )
    val ink = Color.rgb(0xFF, 0xFF, 0xFF)
    val ground = Color.rgb(0x0A, 0x2A, 0x1C)
    val pixels = IntArray(size * size) { i -> if (matrix[i % size, i / size]) ground else ink }
    return Bitmap.createBitmap(pixels, size, size, Bitmap.Config.ARGB_8888)
}
