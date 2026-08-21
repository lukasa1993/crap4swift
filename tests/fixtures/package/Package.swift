// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "Fixture",
    products: [.library(name: "Sample", targets: ["Sample"])],
    targets: [
        .target(name: "Sample"),
        .testTarget(name: "SampleTests", dependencies: ["Sample"]),
    ]
)
