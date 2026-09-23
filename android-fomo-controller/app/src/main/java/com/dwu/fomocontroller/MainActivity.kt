package com.dwu.fomocontroller

import android.app.Activity
import android.content.Intent
import android.graphics.Typeface
import android.os.Bundle
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
import java.text.DateFormat
import java.util.Date

class MainActivity : Activity() {
    private lateinit var prefs: AppPreferences
    private lateinit var db: EventDatabase

    private lateinit var modeSpinner: Spinner
    private lateinit var maxMcInput: EditText
    private lateinit var maxAmountInput: EditText
    private lateinit var copyRatioInput: EditText
    private lateinit var maxAgeInput: EditText
    private lateinit var statusView: TextView
    private lateinit var eventsView: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        prefs = AppPreferences(applicationContext)
        db = EventDatabase(applicationContext)
        AutomationCoordinator.initialize(applicationContext)

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

        maxMcInput = numberInput()
        root.addView(label("Maximum market cap"))
        root.addView(maxMcInput)

        maxAmountInput = numberInput()
        root.addView(label("Maximum source notification amount"))
        root.addView(maxAmountInput)

        copyRatioInput = decimalInput()
        root.addView(label("Copy ratio"))
        root.addView(copyRatioInput)

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

        statusView = TextView(this).apply {
            textSize = 15f
            setPadding(0, 20, 0, 20)
        }
        root.addView(statusView)

        root.addView(Button(this).apply {
            text = "Refresh events"
            setOnClickListener { refresh() }
        })

        root.addView(Button(this).apply {
            text = "Clear local event log"
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

    private fun decimalInput() = EditText(this).apply {
        inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL
    }

    private fun loadSettingsIntoUi() {
        modeSpinner.setSelection(ControllerMode.entries.indexOf(prefs.mode))
        maxMcInput.setText(prefs.maxMarketCap.toString())
        maxAmountInput.setText(prefs.maxSourceAmount.toString())
        copyRatioInput.setText(prefs.copyRatio.toString())
        maxAgeInput.setText(prefs.maxEventAgeSeconds.toString())
    }

    private fun saveSettings() {
        val maxMc = maxMcInput.text.toString().toDoubleOrNull()
        val maxAmount = maxAmountInput.text.toString().toDoubleOrNull()
        val ratio = copyRatioInput.text.toString().toDoubleOrNull()
        val maxAge = maxAgeInput.text.toString().toLongOrNull()

        if (maxMc == null || maxMc <= 0.0 ||
            maxAmount == null || maxAmount <= 0.0 ||
            ratio == null || ratio <= 0.0 ||
            maxAge == null || maxAge <= 0L
        ) {
            Toast.makeText(this, "Enter positive numeric settings.", Toast.LENGTH_LONG).show()
            return
        }

        prefs.maxMarketCap = maxMc
        prefs.maxSourceAmount = maxAmount
        prefs.copyRatio = ratio
        prefs.maxEventAgeSeconds = maxAge
        Toast.makeText(this, "Settings saved.", Toast.LENGTH_SHORT).show()
        refreshStatus()
    }

    private fun refresh() {
        refreshStatus()

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

    private fun refreshStatus() {
        statusView.text = buildString {
            append("Controller: ")
            append(AutomationCoordinator.status())
            append("\nMode: ")
            append(prefs.mode.name)
            append("\nSelectors calibrated: ")
            append(FomoSelectors.calibrated)
            if (!FomoSelectors.calibrated) {
                append("\nPREPARE is fail-closed until Fomo resource IDs are calibrated.")
            }
        }
    }
}
