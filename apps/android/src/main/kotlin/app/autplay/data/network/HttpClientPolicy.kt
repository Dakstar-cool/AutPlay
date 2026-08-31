package app.autplay.data.network

import okhttp3.OkHttpClient

/** Server requests never follow redirects, so credentials cannot cross the trusted origin. */
fun OkHttpClient.withAutPlayRedirectPolicy(): OkHttpClient =
    newBuilder()
        .followRedirects(false)
        .followSslRedirects(false)
        .build()
