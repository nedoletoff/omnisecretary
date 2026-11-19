package ru.nedoletoff.omnisecretary.ui

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext

// Gruvbox dark — палитра проекта (веб, бот, приложение).
val GruvboxBg = Color(0xFF282828)
val GruvboxBg1 = Color(0xFF3C3836)
val GruvboxBg2 = Color(0xFF504945)
val GruvboxFg = Color(0xFFEBDBB2)
val GruvboxGray = Color(0xFFA89984)
val GruvboxYellow = Color(0xFFD79921)
val GruvboxGreen = Color(0xFFB8BB26)
val GruvboxRed = Color(0xFFFB4934)
val GruvboxBlue = Color(0xFF83A598)
val GruvboxAqua = Color(0xFF8EC07C)
val GruvboxOrange = Color(0xFFFE8019)

private val GruvboxColors = darkColorScheme(
    primary = GruvboxYellow,
    onPrimary = GruvboxBg,
    secondary = GruvboxBlue,
    onSecondary = GruvboxBg,
    tertiary = GruvboxAqua,
    background = GruvboxBg,
    onBackground = GruvboxFg,
    surface = GruvboxBg1,
    onSurface = GruvboxFg,
    surfaceVariant = GruvboxBg2,
    onSurfaceVariant = GruvboxGray,
    error = GruvboxRed,
    onError = GruvboxBg,
    outline = GruvboxBg2,
)

@Composable
fun OmniTheme(
    themeMode: String = "gruvbox",
    content: @Composable () -> Unit,
) {
    val dark = isSystemInDarkTheme()
    val colorScheme = when {
        themeMode == "material_you" && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S -> {
            val ctx = LocalContext.current
            if (dark) dynamicDarkColorScheme(ctx) else dynamicLightColorScheme(ctx)
        }
        themeMode == "material_you" -> {
            // Fallback, если dynamic недоступен
            if (dark) darkColorScheme() else lightColorScheme()
        }
        else -> GruvboxColors
    }
    MaterialTheme(colorScheme = colorScheme, typography = Typography(), content = content)
}
