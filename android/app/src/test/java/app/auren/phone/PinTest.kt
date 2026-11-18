package app.auren.phone

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test
import java.io.ByteArrayInputStream
import java.net.InetAddress
import java.security.KeyFactory
import java.security.KeyStore
import java.security.cert.CertificateException
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import java.security.spec.PKCS8EncodedKeySpec
import java.util.Base64
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLHandshakeException
import javax.net.ssl.SSLServerSocket
import javax.net.ssl.SSLSocket

class PinTest {
    // Throwaway self-signed certificates made for this test with openssl (EC P-256), like the PC's own.
    // The key belongs to this test's server only; it guards nothing.
    private val pc = cert("""
-----BEGIN CERTIFICATE-----
MIIBfzCCASWgAwIBAgIUVyNz9vxNbLvx1SfAXlOSckMq4bUwCgYIKoZIzj0EAwIw
FTETMBEGA1UEAwwKQXVyZW4gdGVzdDAeFw0yNjExMTgxOTA4MTBaFw0zNjExMTUx
OTA4MTBaMBUxEzARBgNVBAMMCkF1cmVuIHRlc3QwWTATBgcqhkjOPQIBBggqhkjO
PQMBBwNCAATZT4561Blqgdf71SV34vIpCLzJmHAdk3J05vA8WpykT11PrXUI9gW8
gOO/k9kDCD4TBPSKjQSQf2A/Z4Og0UYEo1MwUTAdBgNVHQ4EFgQU/sRc0AGb9xss
PficknUdCAg71DUwHwYDVR0jBBgwFoAU/sRc0AGb9xssPficknUdCAg71DUwDwYD
VR0TAQH/BAUwAwEB/zAKBggqhkjOPQQDAgNIADBFAiAUlZtkYB12hKgIT3mPxkvs
bT2NQZaizzFP7N7pVQwLiQIhAP2j94nx69D8VdljlvmzuHOZmZ2uuIzmspVfEDh1
reEg
-----END CERTIFICATE-----
""")
    private val other = cert("""
-----BEGIN CERTIFICATE-----
MIIBfjCCASWgAwIBAgIUPUJNBG5pvduzji+CZP3TOfNMTbIwCgYIKoZIzj0EAwIw
FTETMBEGA1UEAwwKTm90IHRoZSBQQzAeFw0yNjExMTgxOTA4MTBaFw0zNjExMTUx
OTA4MTBaMBUxEzARBgNVBAMMCk5vdCB0aGUgUEMwWTATBgcqhkjOPQIBBggqhkjO
PQMBBwNCAAQTnh1z4Eu7qMSHjM7cqjP0ud4000SOaB0bUrh2Q164IR33ZbAPZsOG
JS3hZepDcNiWWMJiJsm2ETXbLq4xjBVvo1MwUTAdBgNVHQ4EFgQUVcSybePs3lUM
S0wNy9qeQs2Sr7owHwYDVR0jBBgwFoAUVcSybePs3lUMS0wNy9qeQs2Sr7owDwYD
VR0TAQH/BAUwAwEB/zAKBggqhkjOPQQDAgNHADBEAiBk2mWyUyWjj3KMC19JYMtu
UTjCwQCgpHvmXVkpIwxs6gIgD6FRkzE92MlNObGPARfmjLNEAUAIYL4Bz4d5EEpA
RAE=
-----END CERTIFICATE-----
""")
    private val pcKey = """
-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgTTe/CFmmhd4TqWjb
h3ZMtkOksWnPVFtOcHLaYgxfaS6hRANCAATZT4561Blqgdf71SV34vIpCLzJmHAd
k3J05vA8WpykT11PrXUI9gW8gOO/k9kDCD4TBPSKjQSQf2A/Z4Og0UYE
-----END PRIVATE KEY-----
"""
    // `openssl x509 -outform DER | sha256sum` of each.
    private val pcFp = "8838ec0185cb0e1e4792b74215c1b6a529f0d16580b5ef11b7a79a9b59b82a02"
    private val otherFp = "ec82bf3d58b5119eeb6956ea69a30c1089d37c782917ed1c2aa4ce2c4b167c7f"

    private fun cert(pem: String) =
        CertificateFactory.getInstance("X.509").generateCertificate(ByteArrayInputStream(pem.trim().toByteArray())) as X509Certificate

    @Test
    fun `the fingerprint is the sha256 of the certificate, as openssl prints it`() {
        assertEquals(pcFp, fingerprint(pc.encoded))
        assertEquals(otherFp, fingerprint(other.encoded))
    }

    @Test
    fun `only the pinned certificate is trusted`() {
        val trust = PinnedTrust(pcFp)
        trust.checkServerTrusted(arrayOf(pc), "ECDHE_ECDSA")
        assertThrows(CertificateException::class.java) { trust.checkServerTrusted(arrayOf(other), "ECDHE_ECDSA") }
        assertThrows(CertificateException::class.java) { trust.checkServerTrusted(arrayOf(), "ECDHE_ECDSA") }
        assertThrows(CertificateException::class.java) { trust.checkServerTrusted(arrayOf(other, pc), "ECDHE_ECDSA") }
        assertEquals(0, trust.acceptedIssuers.size)
    }

    /** A real TLS handshake with a server holding the PC's certificate: the right pin connects, a wrong one doesn't. */
    @Test
    fun `a handshake with the right pin works and a wrong pin is refused`() {
        val der = Base64.getMimeDecoder().decode(pcKey.trim().lines().filterNot { it.startsWith("-----") }.joinToString(""))
        val key = KeyFactory.getInstance("EC").generatePrivate(PKCS8EncodedKeySpec(der))
        val ks = KeyStore.getInstance(KeyStore.getDefaultType()).apply {
            load(null, null)
            setKeyEntry("pc", key, CharArray(0), arrayOf(pc))
        }
        val kmf = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm()).apply { init(ks, CharArray(0)) }
        val ctx = SSLContext.getInstance("TLS").apply { init(kmf.keyManagers, null, null) }
        val server = ctx.serverSocketFactory.createServerSocket(0, 4, InetAddress.getLoopbackAddress()) as SSLServerSocket
        val pool = Executors.newSingleThreadExecutor()
        pool.submit {
            repeat(2) {
                runCatching { (server.accept() as SSLSocket).use { s -> s.startHandshake(); s.outputStream.write(1) } }
            }
        }
        try {
            fun connect(fp: String) =
                (PinnedTrust(fp).socketFactory().createSocket("127.0.0.1", server.localPort) as SSLSocket).use { s ->
                    s.soTimeout = 5_000
                    s.startHandshake()
                    s.inputStream.read()
                }
            assertEquals(1, connect(pcFp))
            assertThrows(SSLHandshakeException::class.java) { connect(otherFp) }
        } finally {
            server.close()
            pool.shutdown()
            pool.awaitTermination(5, TimeUnit.SECONDS)
        }
    }
}
