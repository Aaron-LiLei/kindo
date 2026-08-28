package org.kindo.tv.core

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow

/** mDNS 自动发现的 Hub（§14.1：_kindo._tcp.local，发现只表示可达）。 */
data class DiscoveredHub(
    val baseUrl: String,
    val displayName: String,
    val instanceId: String,
)

/** NsdManager 发现 _kindo._tcp（真实 TV 在家庭局域网内可用；模拟器 NAT 下无结果，
 *  由 Bootstrap 屏提供手动输入兜底，交互 §4.1）。 */
class Discovery(context: Context) {
    private val nsd = context.getSystemService(Context.NSD_SERVICE) as NsdManager

    fun discover(): Flow<DiscoveredHub> = callbackFlow {
        val listener = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(serviceType: String) {}
            override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) {
                close()
            }

            override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {}

            override fun onDiscoveryStopped(serviceType: String) {}

            override fun onServiceFound(serviceInfo: NsdServiceInfo) {
                if (!serviceInfo.serviceType.contains("_kindo._tcp")) return
                // 逐个解析地址；解析成功才下发
                nsd.resolveService(serviceInfo, object : NsdManager.ResolveListener {
                    override fun onResolveFailed(info: NsdServiceInfo, errorCode: Int) {}
                    override fun onServiceResolved(info: NsdServiceInfo) {
                        val host = info.host?.hostAddress ?: return
                        val name = info.attributes["display_name"]
                            ?.toString(Charsets.UTF_8).takeIf { !it.isNullOrBlank() }
                            ?: "Kindo Hub"
                        val instance = info.attributes["instance_id"]?.toString(Charsets.UTF_8) ?: ""
                        trySend(
                            DiscoveredHub(
                                baseUrl = "http://$host:${info.port}",
                                displayName = name,
                                instanceId = instance,
                            ),
                        )
                    }
                })
            }

            override fun onServiceLost(serviceInfo: NsdServiceInfo) {}
        }
        nsd.discoverServices("_kindo._tcp", NsdManager.PROTOCOL_DNS_SD, listener)
        awaitClose { }
    }
}
