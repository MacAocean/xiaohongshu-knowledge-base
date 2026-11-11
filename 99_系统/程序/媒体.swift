import AVFoundation
import CoreMedia
import Foundation
import ImageIO
import UniformTypeIdentifiers

func problem(_ text: String) -> NSError {
    NSError(domain: "KBMedia", code: 1, userInfo: [NSLocalizedDescriptionKey: text])
}

func appendLE<T: FixedWidthInteger>(_ number: T, to data: inout Data) {
    var little = number.littleEndian
    withUnsafeBytes(of: &little) { data.append(contentsOf: $0) }
}

func extract(_ asset: AVURLAsset, into output: URL) async throws {
    guard let track = try await asset.loadTracks(withMediaType: .audio).first else {
        throw problem("媒体没有可读取音轨")
    }
    let reader = try AVAssetReader(asset: asset)
    let format: [String: Any] = [
        AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: 16000,
        AVNumberOfChannelsKey: 1, AVLinearPCMBitDepthKey: 16,
        AVLinearPCMIsFloatKey: false, AVLinearPCMIsBigEndianKey: false,
        AVLinearPCMIsNonInterleaved: false
    ]
    let source = AVAssetReaderTrackOutput(track: track, outputSettings: format)
    guard reader.canAdd(source) else { throw problem("不支持该音轨格式") }
    reader.add(source)
    guard reader.startReading() else { throw reader.error ?? problem("音轨读取失败") }
    try Data(repeating: 0, count: 44).write(to: output)
    let file = try FileHandle(forWritingTo: output)
    defer { try? file.close() }
    try file.seekToEnd()
    var length = 0
    while let sample = source.copyNextSampleBuffer() {
        guard let buffer = CMSampleBufferGetDataBuffer(sample) else { continue }
        let size = CMBlockBufferGetDataLength(buffer)
        var chunk = Data(count: size)
        let copied = chunk.withUnsafeMutableBytes { pointer in
            guard let destination = pointer.baseAddress else { return Int32(0) }
            return CMBlockBufferCopyDataBytes(buffer, atOffset: 0, dataLength: size, destination: destination)
        }
        guard copied == noErr else { throw problem("无法复制 PCM 音轨") }
        length += size
        guard length < Int(UInt32.max) - 36 else { throw problem("音轨超过 WAV 大小上限") }
        try file.write(contentsOf: chunk)
    }
    guard reader.status == .completed, length > 0 else { throw reader.error ?? problem("没有提取到音轨") }
    var header = Data("RIFF".utf8)
    appendLE(UInt32(length + 36), to: &header)
    header.append(contentsOf: "WAVEfmt ".utf8)
    appendLE(UInt32(16), to: &header)
    appendLE(UInt16(1), to: &header)
    appendLE(UInt16(1), to: &header)
    appendLE(UInt32(16000), to: &header)
    appendLE(UInt32(32000), to: &header)
    appendLE(UInt16(2), to: &header)
    appendLE(UInt16(16), to: &header)
    header.append(contentsOf: "data".utf8)
    appendLE(UInt32(length), to: &header)
    try file.seek(toOffset: 0)
    try file.write(contentsOf: header)
}

func frame(_ asset: AVURLAsset, at second: Double, into output: URL) async throws -> Double {
    let generator = AVAssetImageGenerator(asset: asset)
    generator.appliesPreferredTrackTransform = true
    generator.maximumSize = CGSize(width: 1280, height: 1280)
    generator.requestedTimeToleranceBefore = .zero
    generator.requestedTimeToleranceAfter = .zero
    let time = CMTime(seconds: second, preferredTimescale: 1000)
    let result = try await generator.image(at: time)
    guard let destination = CGImageDestinationCreateWithURL(output as CFURL, UTType.png.identifier as CFString, 1, nil)
    else { throw problem("无法创建图片") }
    CGImageDestinationAddImage(destination, result.image, nil)
    guard CGImageDestinationFinalize(destination) else { throw problem("无法保存图片") }
    return result.actualTime.seconds
}

let arguments = CommandLine.arguments
do {
    guard arguments.count >= 3 else { throw problem("需要操作和媒体路径") }
    let asset = AVURLAsset(url: URL(fileURLWithPath: arguments[2]))
    switch arguments[1] {
    case "extract":
        guard arguments.count == 4 else { throw problem("需要 WAV 输出路径") }
        try await extract(asset, into: URL(fileURLWithPath: arguments[3]))
    case "probe":
        let duration = try await asset.load(.duration)
        let audio = try await asset.loadTracks(withMediaType: .audio).count
        let video = try await asset.loadTracks(withMediaType: .video).count
        guard duration.seconds.isFinite else { throw problem("无法读取媒体时长") }
        let data = try JSONSerialization.data(withJSONObject: ["duration": duration.seconds, "audio": audio, "video": video])
        print(String(decoding: data, as: UTF8.self))
    case "frame":
        guard arguments.count == 5, let second = Double(arguments[3]), second >= 0, second.isFinite
        else { throw problem("需要非负的秒数和图片输出路径") }
        print(try await frame(asset, at: second, into: URL(fileURLWithPath: arguments[4])))
    default:
        throw problem("未知的媒体操作")
    }
} catch {
    FileHandle.standardError.write(Data((error.localizedDescription + "\n").utf8))
    exit(1)
}
