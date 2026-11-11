import Foundation
import PDFKit

guard CommandLine.arguments.count == 2,
      let document = PDFDocument(url: URL(fileURLWithPath: CommandLine.arguments[1])) else {
    exit(2)
}
for page in 0..<min(document.pageCount, 250) {
    if let text = document.page(at: page)?.string {
        print(text)
    }
}
