package app.gymgarmin.sync

import android.content.Context
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.TimeUnit

object Sync {
    const val DEFAULT_SERVER = "https://haxfit.vercel.app"
    /** Endereços antigos do app (o projeto mudou de nome): quem tinha um deles salvo passa para o atual. */
    private val OLD_HOSTS = setOf("gymgarmin.vercel.app")

    private fun hostOf(s: String): String =
        s.substringAfter("://").substringBefore('/').substringBefore(':').trim().lowercase()

    fun prefs(ctx: Context) = ctx.getSharedPreferences("gymgarmin", Context.MODE_PRIVATE)

    /** Endereço do HAX salvo, já trocando o antigo pelo atual (e gravando a troca). */
    fun server(ctx: Context): String {
        val p = prefs(ctx)
        val saved = (p.getString("server", DEFAULT_SERVER) ?: DEFAULT_SERVER).trim().trimEnd('/')
        if (saved.isEmpty() || hostOf(saved) in OLD_HOSTS) {
            // o erro guardado era do endereço antigo: some junto com ele
            p.edit().putString("server", DEFAULT_SERVER).remove("last_err").apply()
            return DEFAULT_SERVER
        }
        return saved
    }

    /** Lê o Health Connect e envia. Retorna a mensagem para mostrar na tela. */
    suspend fun run(ctx: Context, days: Long? = null): String {
        val p = prefs(ctx)
        val token = p.getString("token", "")?.trim().orEmpty()
        val server = server(ctx)
        if (token.isEmpty()) throw IllegalStateException("Cole o código pessoal (HAX → Perfil → Relógios → Código pessoal).")
        val since = p.getLong("last_ok", 0L)
        // primeira vez (ou primeira vez desta versão do app): 30 dias; depois, desde o último envio com folga de 2 dias.
        // Quem atualiza o app precisa reler tudo: a versão antiga descartava tipos de treino que esta envia.
        val newVersion = p.getString("app_ver", "") != HealthReader.APP_VERSION
        val n = days ?: if (since == 0L || newVersion) 30L else (((System.currentTimeMillis() - since) / 86_400_000L) + 2).coerceIn(2, 30)
        val payload = HealthReader(ctx).build(n)
        val res = post("$server/api/ingest", token, payload)
        val msg = "Enviado: ${res.optJSONArray("days")?.length() ?: 0} dia(s) de saúde, " +
            "${res.optJSONArray("imported")?.length() ?: 0} treino(s) novo(s)."
        val now = System.currentTimeMillis()
        val stamp = SimpleDateFormat("dd/MM HH:mm", Locale("pt", "BR")).format(Date(now))
        p.edit().putLong("last_ok", now).putString("app_ver", HealthReader.APP_VERSION).putString("last_msg", "$stamp — $msg")
            .remove("last_err").apply()  // deu certo: o erro de antes não aparece mais na tela
        return msg
    }

    private suspend fun post(url: String, token: String, body: JSONObject): JSONObject = withContext(Dispatchers.IO) {
        val c = URL(url).openConnection() as HttpURLConnection
        try {
            c.requestMethod = "POST"
            c.connectTimeout = 20_000
            c.readTimeout = 40_000
            c.doOutput = true
            c.setRequestProperty("Content-Type", "application/json")
            c.setRequestProperty("X-GymGarmin-Token", token)
            c.outputStream.use { it.write(body.toString().toByteArray()) }
            val code = c.responseCode
            val text = (if (code in 200..299) c.inputStream else c.errorStream)?.bufferedReader()?.readText().orEmpty()
            if (code !in 200..299) {
                val detail = runCatching { JSONObject(text).optString("detail") }.getOrNull()
                throw IllegalStateException(detail?.ifBlank { null } ?: "Servidor respondeu $code")
            }
            JSONObject(text)
        } finally {
            c.disconnect()
        }
    }

    fun schedule(ctx: Context) {
        val req = PeriodicWorkRequestBuilder<SyncWorker>(6, TimeUnit.HOURS)
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .build()
        WorkManager.getInstance(ctx).enqueueUniquePeriodicWork("gymgarmin-sync", ExistingPeriodicWorkPolicy.UPDATE, req)
    }
}

class SyncWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result = try {
        Sync.run(applicationContext)
        Result.success()
    } catch (e: Exception) {
        Sync.prefs(applicationContext).edit().putString("last_err", e.message).apply()
        if (runAttemptCount < 3) Result.retry() else Result.failure()
    }
}
