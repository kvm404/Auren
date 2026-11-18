package app.auren.phone

import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.X509TrustManager

/** SHA-256 of a certificate's DER bytes, lowercase hex: what the PC's QR code carries as `fp`. */
fun fingerprint(der: ByteArray): String =
    MessageDigest.getInstance("SHA-256").digest(der).joinToString("") { "%02x".format(it) }

/**
 * Trusts exactly one certificate: the PC's self-signed one, named by the fingerprint the pairing QR code
 * carried. No CA is consulted and nothing else is accepted, so a LAN that isn't trusted can't stand in for
 * the PC. The fingerprint already names the one certificate, so the hostname (a bare IP) isn't checked.
 */
class PinnedTrust(private val fp: String) : X509TrustManager {
    override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
        val leaf = chain?.firstOrNull() ?: throw CertificateException("no certificate")
        val got = fingerprint(leaf.encoded)
        if (got != fp) throw CertificateException("this isn't the paired PC's certificate ($got)")
    }

    override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) =
        throw CertificateException("the app is never a server")

    override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()

    fun socketFactory(): SSLSocketFactory =
        SSLContext.getInstance("TLS").apply { init(null, arrayOf(this@PinnedTrust), null) }.socketFactory

    companion object {
        /** The pin is the identity check; the host is a bare IP from the QR code or the PC's own hello. */
        val ANY_HOST = HostnameVerifier { _, _ -> true }
    }
}
