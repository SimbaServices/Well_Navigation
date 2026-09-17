import SwiftUI

@main
struct WellNavigationApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
                .preferredColorScheme(.dark)
                .statusBarHidden(false)
        }
    }
}
