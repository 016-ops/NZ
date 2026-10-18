// 在编译期校验 Tauri 配置并生成 Windows 资源，不下载或启动游戏文件。
fn main() {
    tauri_build::build()
}
