package app.gymgarmin.sync

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContract
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.PermissionController
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    private lateinit var status: TextView
    private lateinit var server: EditText
    private lateinit var token: EditText

    private val permissions = registerForActivityResult(
        PermissionController.createRequestPermissionResultContract() as ActivityResultContract<Set<String>, Set<String>>
    ) { granted ->
        val ok = granted.containsAll(HealthReader.PERMISSIONS - androidx.health.connect.client.permission.HealthPermission.PERMISSION_READ_HEALTH_DATA_IN_BACKGROUND)
        say(if (ok) "Permissões concedidas. Toque em Sincronizar agora." else "Faltam permissões: sem elas, alguns dados não vão.")
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val p = Sync.prefs(this)
        val dp = resources.displayMetrics.density
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding((20 * dp).toInt(), (28 * dp).toInt(), (20 * dp).toInt(), (20 * dp).toInt())
        }
        fun text(s: String, size: Float = 15f, bold: Boolean = false) = TextView(this).apply {
            text = s; textSize = size; if (bold) setTypeface(typeface, android.graphics.Typeface.BOLD)
            setPadding(0, (8 * dp).toInt(), 0, (4 * dp).toInt())
        }
        fun button(s: String, onClick: () -> Unit) = Button(this).apply { text = s; setOnClickListener { onClick() } }

        col.addView(text("Hybrid Lab Sync", 24f, true))
        col.addView(text("Manda treinos, sono, FC de repouso e passos do Health Connect (Xiaomi, Samsung, Amazfit, Google…) para a sua conta do Hybrid Lab."))
        if (intent?.action == "androidx.health.ACTION_SHOW_PERMISSIONS_RATIONALE" || intent?.action == "android.intent.action.VIEW_PERMISSION_USAGE") {
            col.addView(text("Por que o app pede essas permissões: ele só LÊ seus treinos e dados de saúde e envia para a sua própria conta do Hybrid Lab, para montar seus treinos e acompanhar sua recuperação. Nada é compartilhado com outras pessoas.", 15f))
        }
        col.addView(text("Código pessoal", 14f, true))
        token = EditText(this).apply {
            hint = "gg_…  (Hybrid Lab → Perfil → Outros relógios)"
            setText(p.getString("token", ""))
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD
        }
        col.addView(token)
        col.addView(text("Endereço do app", 14f, true))
        server = EditText(this).apply {
            setText(p.getString("server", Sync.DEFAULT_SERVER)); inputType = InputType.TYPE_TEXT_VARIATION_URI
        }
        col.addView(server)
        col.addView(button("1. Dar permissões do Health Connect") { askPermissions() })
        col.addView(button("2. Sincronizar agora") { syncNow() })
        status = text(p.getString("last_msg", null)?.let { "Último envio: $it" } ?: "Nenhum envio ainda.", 14f)
        col.addView(status)
        p.getString("last_err", null)?.let { col.addView(text("Último erro automático: $it", 13f)) }
        col.addView(text("Depois do primeiro envio, o app manda sozinho a cada ~6 horas (com internet).", 13f))
        setContentView(ScrollView(this).apply { addView(col, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT)) })
        col.gravity = Gravity.TOP
    }

    private fun save() {
        Sync.prefs(this).edit()
            .putString("token", token.text.toString().trim())
            .putString("server", server.text.toString().trim().ifEmpty { Sync.DEFAULT_SERVER })
            .apply()
    }

    private fun healthConnectReady(): Boolean {
        return when (HealthConnectClient.getSdkStatus(this)) {
            HealthConnectClient.SDK_AVAILABLE -> true
            HealthConnectClient.SDK_UNAVAILABLE_PROVIDER_UPDATE_REQUIRED -> {
                say("Atualize o Health Connect na Play Store e volte aqui.")
                startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("market://details?id=com.google.android.apps.healthdata")))
                false
            }
            else -> {
                say("Este celular não tem o Health Connect. Em Android 9 a 13, instale \"Health Connect\" na Play Store.")
                runCatching { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("market://details?id=com.google.android.apps.healthdata"))) }
                false
            }
        }
    }

    private fun askPermissions() {
        save()
        if (!healthConnectReady()) return
        permissions.launch(HealthReader.PERMISSIONS)
    }

    private fun syncNow() {
        save()
        if (!healthConnectReady()) return
        say("Lendo o Health Connect e enviando…")
        lifecycleScope.launch {
            try {
                if (!HealthReader(this@MainActivity).grantedAll()) {
                    say("Dê as permissões primeiro (botão 1)."); return@launch
                }
                val msg = Sync.run(this@MainActivity)
                Sync.schedule(this@MainActivity)
                say("$msg\nPronto: a partir de agora envia sozinho a cada ~6 h.")
            } catch (e: Exception) {
                say("Não deu certo: ${e.message}")
            }
        }
    }

    private fun say(s: String) { status.text = s }
}
