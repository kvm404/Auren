package app.auren.phone

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle

/**
 * The tile's way in: starts a hands-free conversation, opens Auren and goes. It isn't exported, so no other app can
 * open it to turn the microphone on; the tile reaches it through its own PendingIntent.
 */
class TalkActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Remote.store.pc != null && checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            if (Remote.link.value.kind == LinkKind.CONNECTED) Remote.startConversation()
            else Remote.talkWhenConnected = true
        }
        startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT))
        finish()
    }
}
