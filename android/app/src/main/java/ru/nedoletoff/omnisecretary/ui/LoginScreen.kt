package ru.nedoletoff.omnisecretary.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import ru.nedoletoff.omnisecretary.data.Api
import ru.nedoletoff.omnisecretary.data.Session

@Composable
fun LoginScreen(onLoggedIn: () -> Unit) {
    var server by remember { mutableStateOf(Session.server) }
    var login by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf("") }
    val scope = rememberCoroutineScope()

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text("OmniSecretary", style = MaterialTheme.typography.headlineMedium,
            color = GruvboxYellow)
        Text("Личный секретарь на вашем Raspberry Pi",
            style = MaterialTheme.typography.bodyMedium, color = GruvboxGray)
        Spacer(Modifier.height(12.dp))
        Text(
            "Чаты с ИИ, голос, календарь и задачи, заметки в Obsidian — " +
                "всё на вашем сервере. Данные не покидают ваш Pi.",
            style = MaterialTheme.typography.bodySmall, color = GruvboxGray,
        )
        Spacer(Modifier.height(28.dp))

        OutlinedTextField(
            value = server,
            onValueChange = { server = it },
            label = { Text("Адрес сервера") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
            supportingText = { Text("Например, https://nedoletoff.sknt.ru", color = GruvboxGray) },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri,
                imeAction = ImeAction.Next),
        )
        Spacer(Modifier.height(12.dp))
        OutlinedTextField(
            value = login,
            onValueChange = { login = it },
            label = { Text("Логин") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next),
        )
        Spacer(Modifier.height(12.dp))
        OutlinedTextField(
            value = password,
            onValueChange = { password = it },
            label = { Text("Пароль") },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            modifier = Modifier.fillMaxWidth(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password,
                imeAction = ImeAction.Done),
        )
        if (error.isNotBlank()) {
            Spacer(Modifier.height(12.dp))
            Text(error, color = GruvboxRed, style = MaterialTheme.typography.bodySmall)
        }
        Spacer(Modifier.height(20.dp))
        Button(
            onClick = {
                if (server.isBlank() || login.isBlank() || password.isBlank()) {
                    error = "Заполните все поля"
                    return@Button
                }
                busy = true
                error = ""
                scope.launch {
                    try {
                        val response = Api(server).signIn(login.trim(), password)
                        val token = response.optString("token")
                        if (token.isBlank()) error("Сервер не вернул токен")
                        else {
                            Session.save(server, token,
                                response.optString("email", login),
                                response.optString("role", "user"))
                            onLoggedIn()
                        }
                    } catch (failure: Exception) {
                        error = failure.message ?: "Не удалось войти. Проверьте адрес и сеть."
                    } finally {
                        busy = false
                    }
                }
            },
            enabled = !busy,
            modifier = Modifier.fillMaxWidth(),
        ) {
            if (busy) CircularProgressIndicator(modifier = Modifier.height(18.dp))
            else Text("Войти")
        }
        Spacer(Modifier.height(16.dp))
        Text("Нет аккаунта? Регистрация на сервере: /register",
            style = MaterialTheme.typography.bodySmall, color = GruvboxGray)
        Spacer(Modifier.height(8.dp))
        Text(
            "Совет: добавьте виджет или плитку быстрого доступа — " +
                "голосовой запрос запускается в одно касание.",
            style = MaterialTheme.typography.bodySmall, color = GruvboxGray,
        )
    }
}
