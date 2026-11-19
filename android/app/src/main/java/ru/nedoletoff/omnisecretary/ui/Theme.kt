package ru.nedoletoff.omnisecretary.ui

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext

// Палитра Gruvbox (фирменная тема).
private val GbBg = Color(0xFF282828)
private val GbBg1 = Color(0xFF3C3836)
private val GbBg2 = Color(0xFF504945)
private val GbFg = Color(0xFFEBDBB2)
private val GbGray = Color(0xFFA89984)
private val GbYellow = Color(0xFFD79921)
private val GbGreen = Color(0xFFB8BB26)
private val GbRed = Color(0xFFFB4934)
private val GbBlue = Color(0xFF83A598)
private val GbAqua = Color(0xFF8EC07C)
private val GbOrange = Color(0xFFFE8019)

/** Семантические цвета приложения — независимы для каждой темы. */
data class AppColors(
    val background: Color,
    val surface: Color,
    val surfaceAlt: Color,
    val text: Color,
    val textSecondary: Color,
    val accent: Color,
    val onAccent: Color,
    val action: Color,
    val success: Color,
    val info: Color,
    val warning: Color,
    val danger: Color,
)

private val GruvboxAppColors = AppColors(
    background = GbBg,
    surface = GbBg1,
    surfaceAlt = GbBg2,
    text = GbFg,
    textSecondary = GbGray,
    accent = GbYellow,
    onAccent = GbBg,
    action = GbBlue,
    success = GbGreen,
    info = GbAqua,
    warning = GbOrange,
    danger = GbRed,
)

private val GruvboxScheme = darkColorScheme(
    primary = GbYellow,
    onPrimary = GbBg,
    secondary = GbBlue,
    onSecondary = GbBg,
    tertiary = GbAqua,
    background = GbBg,
    onBackground = GbFg,
    surface = GbBg1,
    onSurface = GbFg,
    surfaceVariant = GbBg2,
    onSurfaceVariant = GbGray,
    error = GbRed,
    onError = GbBg,
    outline = GbBg2,
)

val LocalAppColors = staticCompositionLocalOf { GruvboxAppColors }

object OmniPalette {
    val colors: AppColors
        @Composable @ReadOnlyComposable get() = LocalAppColors.current
}

private fun appColorsFromScheme(scheme: ColorScheme): AppColors = AppColors(
    background = scheme.background,
    surface = scheme.surface,
    surfaceAlt = scheme.surfaceVariant,
    text = scheme.onBackground,
    textSecondary = scheme.onSurfaceVariant,
    accent = scheme.primary,
    onAccent = scheme.onPrimary,
    action = scheme.secondary,
    success = scheme.tertiary,
    info = scheme.secondaryContainer.let { c ->
        // для текста/иконок берём контентный цвет контейнера — контрастный
        scheme.onSecondaryContainer
    },
    warning = scheme.tertiaryContainer.let { scheme.onTertiaryContainer },
    danger = scheme.error,
)

private fun schemeToAppColors(scheme: ColorScheme, isDynamic: Boolean): AppColors {
    return if (isDynamic) {
        // Material You: все цвета только из динамической схемы, без Gruvbox.
        AppColors(
            background = scheme.background,
            surface = scheme.surface,
            surfaceAlt = scheme.surfaceVariant,
            text = scheme.onSurface,
            textSecondary = scheme.onSurfaceVariant,
            accent = scheme.primary,
            onAccent = scheme.onPrimary,
            action = scheme.secondary,
            success = scheme.tertiary,
            info = scheme.primary.copy(alpha = 0.85f),
            warning = scheme.error.copy(alpha = 0.85f),
            danger = scheme.error,
        )
    } else {
        appColorsFromScheme(scheme)
    }
}

@Composable
fun OmniTheme(
    themeMode: String = "gruvbox",
    content: @Composable () -> Unit,
) {
    val dark = isSystemInDarkTheme()
    val (scheme, appColors) = when {
        themeMode == "material_you" && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S -> {
            val ctx = LocalContext.current
            val s = if (dark) dynamicDarkColorScheme(ctx) else dynamicLightColorScheme(ctx)
            s to schemeToAppColors(s, isDynamic = true)
        }
        themeMode == "material_you" -> {
            val s = if (dark) darkColorScheme() else lightColorScheme()
            s to schemeToAppColors(s, isDynamic = true)
        }
        else -> GruvboxScheme to GruvboxAppColors
    }
    CompositionLocalProvider(LocalAppColors provides appColors) {
        MaterialTheme(colorScheme = scheme, typography = Typography(), content = content)
    }
}

// Совместимость: старые имена больше не экспортируются, используйте LocalAppColors.current / OmniPalette.colors.
