import SwiftUI

struct SplashView: View {
    var body: some View {
        VStack(spacing: 14) {
            Text("◆")
                .font(.system(size: 56, weight: .semibold))
                .foregroundStyle(Color(red: 0.83, green: 0.63, blue: 0.09))
            Text("Well Navigation")
                .font(.system(size: 26, weight: .semibold, design: .default))
                .foregroundStyle(Color(red: 0.91, green: 0.93, blue: 0.85))
            Text("TX · NM · OK · LA")
                .font(.subheadline)
                .foregroundStyle(Color(red: 0.60, green: 0.64, blue: 0.53))
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(red: 0.07, green: 0.09, blue: 0.06))
    }
}
