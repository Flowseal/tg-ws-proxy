package com.tgwsproxy

import com.chaquo.python.Python
import org.json.JSONArray
import org.json.JSONObject

object ConnectivityTest {
    const val CFPROXY_AUTO = "cfproxy_auto"
    const val CFPROXY_MULTI = "cfproxy_multi"
    const val CFWORKER_MULTI = "cfworker_multi"

    data class Result(val best: String, val results: Map<String, Map<Int, Any>>)

    fun run(kind: String, domains: List<String>): Result {
        val json = JSONArray(domains).toString()
        val raw = Python.getInstance().getModule("tg_android")
            .callAttr("run_test", kind, json).toString()
        val root = JSONObject(raw)
        val byDomain = root.getJSONObject("results")
        val out = LinkedHashMap<String, Map<Int, Any>>()
        for (domain in byDomain.keys()) {
            val dcs = byDomain.getJSONObject(domain)
            val row = LinkedHashMap<Int, Any>()
            for (dc in dcs.keys()) row[dc.toInt()] = dcs.get(dc)
            out[domain] = row
        }
        return Result(root.optString("best"), out)
    }
}
