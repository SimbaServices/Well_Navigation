import SwiftUI

struct OfflineView: View {
    var onRetry: () -> Void

    var body: some View {
        VStack(spacing: 16) {
            Text("◆")
                .font(.system(size: 40, weight: .semibold))
                .foregroundStyle(Color(red: 0.83, green: 0.63, blue: 0.09))
            Text("No network")
                .font(.title2.weight(.semibold))
                .foregroundStyle(Color(red: 0.91, green: 0.93, blue: 0.85))
            Text("Search needs a connection. If you already saved a map view, open the app again when you have signal once, then the USGS tiles and pinned wells stay available offline.")
                .multilineTextAlignment(.center)
                .foregroundStyle(Color(red: 0.60, green: 0.64, blue: 0.53))
                .padding(.horizontal, 28)
            Button("Try again", action: onRetry)
                .buttonStyle(.borderedProminent)
                .tint(Color(red: 0.49, green: 0.70, blue: 0.42))
                .foregroundStyle(Color(red: 0.06, green: 0.13, blue: 0.05))
        }
        .padding(.bottom, 24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
