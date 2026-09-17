import Network
import SwiftUI

struct ContentView: View {
    @StateObject private var network = NetworkMonitor()
    @State private var showSplash = true

    var body: some View {
        ZStack {
            Color(red: 0.07, green: 0.09, blue: 0.06).ignoresSafeArea()
            if showSplash {
                SplashView()
            } else {
                WebContainer(startURL: AppConfig.startURL)
                    .ignoresSafeArea()
                if !network.isOnline {
                    VStack {
                        Text("Offline — saved map tiles and pinned wells still work")
                            .font(.footnote.weight(.medium))
                            .foregroundStyle(Color(red: 0.83, green: 0.63, blue: 0.09))
                            .multilineTextAlignment(.center)
                            .padding(.horizontal, 16)
                            .padding(.vertical, 8)
                            .frame(maxWidth: .infinity)
                            .background(Color(red: 0.24, green: 0.18, blue: 0.04))
                        Spacer()
                    }
                    .allowsHitTesting(false)
                }
            }
        }
        .onAppear {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.15) {
                withAnimation(.easeOut(duration: 0.25)) {
                    showSplash = false
                }
            }
        }
    }
}

enum AppConfig {
    static var startURL: URL {
        if let raw = Bundle.main.object(forInfoDictionaryKey: "WELLNAVStartURL") as? String,
           let url = URL(string: raw), url.scheme == "https" {
            return url
        }
        return URL(string: "https://wellnav.simba.services")!
    }

    static var allowedHosts: Set<String> {
        var hosts: Set<String> = ["wellnav.simba.services"]
        if let host = startURL.host {
            hosts.insert(host)
        }
        return hosts
    }
}

final class NetworkMonitor: ObservableObject {
    @Published var isOnline = true
    private let monitor = NWPathMonitor()
    private let queue = DispatchQueue(label: "wellnav.network")

    init() {
        monitor.pathUpdateHandler = { [weak self] path in
            DispatchQueue.main.async {
                self?.isOnline = path.status == .satisfied
            }
        }
        monitor.start(queue: queue)
    }

    func refresh() {
        isOnline = monitor.currentPath.status == .satisfied
    }
}
