package com.dwu.fomocontroller.model

enum class ControllerMode {
    OBSERVE,
    DRY_RUN,
    PREPARE;

    companion object {
        fun from(raw: String?): ControllerMode =
            entries.firstOrNull { it.name == raw } ?: OBSERVE
    }
}
