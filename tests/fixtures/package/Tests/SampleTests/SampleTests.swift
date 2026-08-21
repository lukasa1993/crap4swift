import XCTest
@testable import Sample

final class SampleTests: XCTestCase {
    func testChoose() {
        XCTAssertEqual(choose(true, true), 1)
        XCTAssertEqual(choose(false, true), 0)
    }
}
