package ru.nedoletoff.omnisecretary.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// Gruvbox dark — та же палитра, что в вебе и на аватаре бота.
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

private val colors = darkColorScheme(
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
fun OmniTheme(content: @Composable () -> Unit) {
    // Тёмная тема всегда: Gruvbox — это и есть оформление проекта.
    @Suppress("UNUSED_EXPRESSION")
    isSystemInDarkTheme()
    MaterialTheme(colorScheme = colors, typography = Typography(), content = content)
}
