package com.dwu.fomocontroller

import android.app.Activity
import android.content.Intent
import android.graphics.Typeface
import android.os.Bundle
import android.os.Environment
import android.provider.Settings
import android.text.InputType
import android.view.View
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.TextView
import android.widget.Toast
import com.dwu.fomocontroller.automation.AutomationCoordinator
import com.dwu.fomocontroller.automation.FomoSelectors
import com.dwu.fomocontroller.config.AppPreferences
import com.dwu.fomocontroller.data.EventDatabase
import com.dwu.fomocontroller.model.ControllerMode
import com.dwu.fomocontroller.strategy.AdaptiveHybridEngine
import java.io.File
import java.text.DateFormat
import java.util.Date

class MainActivity : Activity() {
    private lateinit var prefs: AppPreferences
    private lateinit var db: EventDatabase

    private lateinit var modeSpinner: Spinner
    private lateinit var feeSpinner: Spinner
    private lateinit var maxAgeInput: EditText
    private lateinit var statusView: TextView
    private lateinit var recorderView: TextView
    private lateinit var eventsView: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        prefs = AppPreferences(applicationContext)
        db = EventDatabase(applicationContext)
        AutomationCoordinator.initialize(applicationContext)
        AdaptiveHybridEngine.initialize(applicationContext)

        setContentView(buildUi())
        loadSettingsIntoUi()
        refresh()
    }

    override fun onResume() {
        super.onResume()
        refresh()
    }

    private fun buildUi(): View {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(32, 32, 32, 48)
        }

        root.addView(TextView(this).apply {
            text = "Fomo Controller"
            textSize = 26f
            setTypeface(typeface, Typeface.BOLD)
        })

        root.addView(TextView(this).apply {
            text = "All runtime notification/UI control stays inside Android. Start with OBSERVE, then DRY_RUN."
            textSize = 15f
            setPadding(0, 8, 0, 18)
        })

        root.addView(label("Mode"))
        modeSpinner = Spinner(this)
        val modes = ControllerMode.entries.map { it.name }
        modeSpinner.adapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_dropdown_item,
            modes
        )
        modeSpinner.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                prefs.mode = ControllerMode.entries[position]
                refreshStatus()
            }
            override fun onNothingSelected(parent: AdapterView<*>?) = Unit
        }
        root.addView(modeSpinner)

        root.addView(label("Adaptive Hybrid v2 fee schedule"))
        feeSpinner = Spinner(this).apply {
            adapter = ArrayAdapter(
                this@MainActivity,
                android.R.layout.simple_spinner_dropdown_item,
                listOf("Standard: max(\$0.95, 0.50%)", "10% code: max(\$0.855, 0.45%)")
            )
        }
        root.addView(feeSpinner)

        maxAgeInput = numberInput()
        root.addView(label("Maximum event age (seconds)"))
        root.addView(maxAgeInput)

        root.addView(Button(this).apply {
            text = "Save settings"
            setOnClickListener { saveSettings() }
        })

        root.addView(Button(this).apply {
            text = "Notification access"
            setOnClickListener {
                startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
            }
        })

        root.addView(Button(this).apply {
            text = "Accessibility settings"
            setOnClickListener {
                startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
            }
        })

        root.addView(Button(this).apply {
            text = "Pause / clear automation queue"
            setOnClickListener {
                AutomationCoordinator.pauseAndClearQueue()
                refreshStatus()
            }
        })

        root.addView(Button(this).apply {
            text = "Confirm latest prepared trade executed"
            setOnClickListener {
                Toast.makeText(
                    this@MainActivity,
                    AdaptiveHybridEngine.confirmLatestPrepared(),
                    Toast.LENGTH_LONG
                ).show()
                refresh()
            }
        })

        root.addView(Button(this).apply {
            text = "Reject latest prepared trade"
            setOnClickListener {
                Toast.makeText(
                    this@MainActivity,
                    AdaptiveHybridEngine.rejectLatestPrepared(),
                    Toast.LENGTH_LONG
                ).show()
                refresh()
            }
        })

        statusView = TextView(this).apply {
            textSize = 15f
            setPadding(0, 20, 0, 12)
        }
        root.addView(statusView)

        recorderView = TextView(this).apply {
            textSize = 15f
            setPadding(0, 8, 0, 12)
        }
        root.addView(recorderView)

        root.addView(Button(this).apply {
            text = "Export raw buy/sell CSV"
            setOnClickListener { exportRawRecorder() }
        })

        root.addView(Button(this).apply {
            text = "Refresh events"
            setOnClickListener { refresh() }
        })

        root.addView(Button(this).apply {
            text = "Clear controller state log"
            setOnClickListener {
                db.clear()
                refresh()
            }
        })

        root.addView(TextView(this).apply {
            text = "Recent events"
            textSize = 20f
            setTypeface(typeface, Typeface.BOLD)
            setPadding(0, 24, 0, 8)
        })

        eventsView = TextView(this).apply {
            textSize = 13f
            setTextIsSelectable(true)
            typeface = Typeface.MONOSPACE
        }
        root.addView(eventsView)

        return ScrollView(this).apply {
            addView(root)
        }
    }

    private fun label(textValue: String) = TextView(this).apply {
        text = textValue
        textSize = 14f
        setTypeface(typeface, Typeface.BOLD)
        setPadding(0, 14, 0, 2)
    }

    private fun numberInput() = EditText(this).apply {
        inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL
    }

    private fun loadSettingsIntoUi() {
        modeSpinner.setSelection(ControllerMode.entries.indexOf(prefs.mode))
        feeSpinner.setSelection(if (prefs.feeDiscount == 0.10) 1 else 0)
        maxAgeInput.setText(prefs.maxEventAgeSeconds.toString())
    }

    private fun saveSettings() {
        val maxAge = maxAgeInput.text.toString().toLongOrNull()

        if (maxAge == null || maxAge <= 0L) {
            Toast.makeText(this, "Enter a positive maximum event age.", Toast.LENGTH_LONG).show()
            return
        }

        prefs.feeDiscount = if (feeSpinner.selectedItemPosition == 1) 0.10 else 0.0
        prefs.maxEventAgeSeconds = maxAge
        Toast.makeText(this, "Settings saved.", Toast.LENGTH_SHORT).show()
        refreshStatus()
    }

    private fun refresh() {
        refreshStatus()
        refreshRecorderStatus()

        val format = DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.MEDIUM)
        val rows = db.recent(40)
        eventsView.text = if (rows.isEmpty()) {
            "No events captured yet."
        } else {
            rows.joinToString("\n\n") { event ->
                buildString {
                    append(format.format(Date(event.capturedTime)))
                    append("  ")
                    append(event.state)
                    append("\n")
                    append(event.action ?: "-")
                    append("  ")
                    append(event.trader ?: "-")
                    append("  ")
                    append(event.coin ?: "-")
                    append("\nMC=")
                    append(event.marketCap ?: "-")
                    append(" source=")
                    append(event.sourceAmount ?: "-")
                    append(" copy=")
                    append(event.copyAmount ?: "-")
                    append("\n")
                    append(event.title)
                    append("\n")
                    append(event.rawText)
                    if (!event.failureReason.isNullOrBlank()) {
                        append("\nreason=")
                        append(event.failureReason)
                    }
                }
            }
        }
    }

    private fun refreshRecorderStatus() {
        val stats = db.recorderStats()
        val latest = stats.latestPostTime?.let {
            DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.MEDIUM)
                .format(Date(it))
        } ?: "none yet"
        recorderView.text = buildString {
            append("Raw recorder: ")
            append(stats.total)
            append(" rows")
            append("\nBuys: ")
            append(stats.buys)
            append("   Sells: ")
            append(stats.sells)
            append("\nLatest post: ")
            append(latest)
            append("\nDatabase: ")
            append(getDatabasePath(EventDatabase.DB_NAME).absolutePath)
        }
    }

    private fun exportRawRecorder() {
        val directory = getExternalFilesDir(Environment.DIRECTORY_DOCUMENTS)
            ?: File(filesDir, "exports")
        val destination = File(directory, "fomo_buy_sell_notifications.csv")
        Thread {
            runCatching { db.exportRecordedCsv(destination) }
                .onSuccess { rows ->
                    runOnUiThread {
                        Toast.makeText(
                            this,
                            "Exported $rows raw rows to ${destination.absolutePath}",
                            Toast.LENGTH_LONG
                        ).show()
                    }
                }
                .onFailure { error ->
                    runOnUiThread {
                        Toast.makeText(
                            this,
                            "Raw export failed: ${error.message}",
                            Toast.LENGTH_LONG
                        ).show()
                    }
                }
        }.start()
    }

    private fun refreshStatus() {
        val strategy = AdaptiveHybridEngine.status()
        statusView.text = buildString {
            append("Controller: ")
            append(AutomationCoordinator.status())
            append("\nMode: ")
            append(prefs.mode.name)
            append("\nModel: ")
            append(AdaptiveHybridEngine.modelDescription())
            append("\nStrategy: mode=")
            append(strategy.mode)
            append(" stage=")
            append(strategy.stageRatio)
            append(" equity=$")
            append("%.2f".format(java.util.Locale.US, strategy.equity))
            append(" cash=$")
            append("%.2f".format(java.util.Locale.US, strategy.cash))
            append(" open=$")
            append("%.2f".format(java.util.Locale.US, strategy.openCost))
            append("\nPositions: ")
            append(strategy.openPositions)
            append("   Pending plans: ")
            append(strategy.pendingPlans)
            if (strategy.latestPreparedKey != null) {
                append("\nA prepared trade awaits confirmation or rejection.")
            }
            append("\nSelectors calibrated: ")
            append(FomoSelectors.calibrated)
            if (!FomoSelectors.calibrated) {
                append("\nPREPARE is fail-closed until Fomo resource IDs are calibrated.")
            }
        }
    }
}
