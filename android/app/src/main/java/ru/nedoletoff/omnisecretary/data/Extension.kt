package ru.nedoletoff.omnisecretary.data

import retrofit2.Response

/** Безопасный вызов Retrofit-ответа: ошибка сразу текстом для UI. */
suspend fun <T> safeCall(call: suspend () -> Response<T>): T {
    val response = call()
    if (!response.isSuccessful) throw Exception("HTTP ${response.code()}: ${response.errorBody()?.string().orEmpty()}")
    return response.body() ?: throw Exception("Пустой ответ сервера")
}
