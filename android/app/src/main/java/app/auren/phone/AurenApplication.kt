package app.auren.phone

import android.app.Application

class AurenApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        Remote.init(this)
        Updates.init(this)
    }
}
