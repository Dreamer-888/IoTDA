# 白黄硬件界面设计核对

- source visual truth: design/reference-light-hardware.png（本轮第 1 张，1486×1058）
- initial implementation: design/light-hardware-initial.jpg（1486×1058 CSS 视口；浏览器原生截图为1471×1047，排除滚动条后略有缩放）
- full comparison: design/light-hardware-comparison-initial.jpg
- state: 演示设备01、实时监测、加速度、原始数据、60秒。数值与时间持续更新。

## 第一轮
- [P2] 标题和设备尺寸偏小：增大标题到60px、模型到1.45倍。
- [P2] 设备投影过于清晰：降低透明度、提高阴影模糊并将灯光移至上方。
- [P2] 桌面内容超出视口17px：调整底部留白和读数间距，保留完整操作区。
- [P2] 顶部导航偏右：移除旧布局的50px左偏移。

## 待完成
- 修复后重新截图和并排对比；桌面/手机交互回归与控制台检查。

final result: blocked
