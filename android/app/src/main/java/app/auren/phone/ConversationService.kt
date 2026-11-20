package app.auren.phone

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.drawable.Icon
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

/**
 * Keeps a hands-free conversation going with the screen off. Without it, Android stops the app's microphone
 * and network once no screen of it shows, and Remote ends the conversation when the last screen goes.
 *
 * While conversation mode is on, this foreground service (type microphone, with a notification and an End
 * button) holds Remote's link like a screen does, and a partial wake lock keeps the CPU awake between turns.
 * It stops itself as soon as conversation mode goes off, for whatever reason, and lets go of the link: with
 * no screen showing, Remote then closes it as usual.
 *
 * Android allows a microphone service only while the app is on screen, which it is: Hands-free is tapped on
 * Home, and the tile opens the app first.
 */
class ConversationService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var holding = false
    private var wake: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_END) {           // the notification's End: started plainly, not as foreground
            Remote.stopConversation()
            if (!holding) stopSelf()
            return START_NOT_STICKY
        }
        // Every startForegroundService needs its startForeground, even when this is already running.
        val n = notification()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            startForeground(NOTIFICATION_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        } else {
            startForeground(NOTIFICATION_ID, n)
        }
        if (!holding) {
            holding = true
            Remote.start()                            // like a screen: the link stays while this runs
            wake = getSystemService(PowerManager::class.java)
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "auren:handsfree")
                .apply {
                    setReferenceCounted(false)
                    acquire(MAX_MS)
                }
            // Off for any reason (tapped off, Stop, went quiet, busy, error, the link): stop. It may be off already.
            scope.launch {
                Remote.conversation.first { !it }
                stopSelf()
            }
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        scope.cancel()
        wake?.let { if (it.isHeld) it.release() }
        wake = null
        if (holding) {
            holding = false
            Remote.stop()
        }
        super.onDestroy()
    }

    private fun notification(): Notification {
        val nm = getSystemService(NotificationManager::class.java)
        if (nm.getNotificationChannel(CHANNEL) == null) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL, getString(R.string.handsfree_channel), NotificationManager.IMPORTANCE_LOW).apply {
                    description = getString(R.string.handsfree_channel_about)
                    setShowBadge(false)
                },
            )
        }
        val open = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val end = PendingIntent.getService(
            this, 1,
            Intent(this, ConversationService::class.java).setAction(ACTION_END),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return Notification.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_tile)
            .setContentTitle(getString(R.string.handsfree_on))
            .setContentText(getString(R.string.handsfree_on_about))
            .setContentIntent(open)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setCategory(Notification.CATEGORY_CALL)
            .addAction(Notification.Action.Builder(Icon.createWithResource(this, R.drawable.ic_tile), getString(R.string.handsfree_end), end).build())
            .build()
    }

    companion object {
        private const val CHANNEL = "handsfree"
        private const val NOTIFICATION_ID = 7
        private const val ACTION_END = "app.auren.phone.END_HANDSFREE"
        private const val MAX_MS = 2 * 60 * 60_000L      // a safety cap on the wake lock; the mode ends long before

        /** Conversation mode just went on, with the app on screen and the microphone allowed. */
        fun start(context: Context) {
            // Refused only if the app is somehow in the background: the conversation still runs while it shows.
            runCatching { context.startForegroundService(Intent(context, ConversationService::class.java)) }
        }
    }
}
