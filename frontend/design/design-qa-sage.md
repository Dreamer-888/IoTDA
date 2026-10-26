# 暖灰青绿配色验收

日期：2026-10-14。范围：用户选择的第 2 张图片及随后再次上传的同款截图。沿用现有业务、布局、响应式规则和交互。

final result: passed

## Findings

无遗留 P0/P1/P2 问题。已修复以下两处：

- [P2，已修复] 初次浅色场景的传感器面板偏暗。依据 design/sage-comparison-initial.jpg，主体材质比选定图更暗；将三维场景曝光从 0.85 调整至 1.15，保留可旋转模型与原始数据驱动。修复后重新捕获并查看 design/sage-comparison.jpg 和 design/sage-pose-comparison.jpg，材质明暗接近目标。
- [P2，已修复] 登录页的“已安全退出”沿用错误提示色。初始证据 design/sage-login-initial.jpg。将成功与失败提示分色，退出和修改密码成功使用青绿，提交失败恢复错误色。修复证据 design/sage-login-mobile.jpg；浏览器读取成功提示色为 rgb(17, 101, 77)。

## 对比依据

- source visual truth: C:/Users/18246/Desktop/frontend/frontend/design/reference-sage.png
- implementation screenshot: C:/Users/18246/Desktop/frontend/frontend/design/sage-desktop.jpg
- implementation URL: http://127.0.0.1:5173/?demo=1#monitor
- CSS viewport：1486 × 1058。源图像素与最终浏览器截图均为 1486 × 1058，直接按 1:1 并排，无缩放归一化。
- 状态：演示用户、第一台传感器、默认三维视角、加速度、60 秒、原始数据、统计折叠。读数、时间和曲线随上报动态变化，因此与参考图数字不同。
- full-view evidence: design/sage-comparison.jpg，左为参考，右为实现。
- focused evidence: design/sage-pose-comparison.jpg、design/sage-readings-comparison.jpg、design/sage-controls-comparison.jpg，均为相同坐标区域并排。
- comparison history: design/sage-comparison-initial.jpg → 调整曝光 → design/sage-comparison.jpg；登录成功提示的前后证据见 Findings。
- 旧深色阶段的报告保存在 design/design-qa-dark.md，作为已有业务验证记录，不再作为当前视觉基准。

## 五项保真检查

| 检查项 | 结果 |
| --- | --- |
| 字体与排版 | 保留 Segoe UI / Microsoft YaHei / PingFang SC 字体栈；标题、等宽数字、表单标签和正文层次稳定，无新增换行或截断。浏览器字体抗锯齿与生成图略有差异，属于 P3。 |
| 间距与布局 | 保留顶部导航、左右姿态区域、通栏曲线的位置、比例、边距及圆角；增加参考中的浅绿导航选中底色。没有重排信息或改变首屏密度。 |
| 颜色与状态 | 背景 #eef1e9，面板 #f7f9f4，青绿强调 #11654d，正文 #1c302a，次要文本 #607067；同步输入框、下拉、日期控件、菜单、弹窗、空状态及成功/错误提示。图表与三维通过 lib/theme.js 读取 CSS 主题变量，曲线与图例颜色一致。 |
| 图像质量 | 复用山峰头像及传感器面板纹理，保留真正可交互的三维传感器。浅色场景、淡绿网格与中性照明符合目标；视角和轮廓保持原实现。图片中阴影、网格透视及抗锯齿与实时 WebGL 渲染有细节差异，属于 P3。 |
| 文案与内容 | 所有中文导航、表单、读数、单位、状态及历史语义保留；演示模式标记明确。没有添加图片没有依据的新功能。 |

抽样计算对比度：正文/面板 13.16:1，次要文字/面板 4.94:1，强调文字/面板 6.62:1，错误文字/面板 5.03:1，主按钮文字/底色 6.81:1。这是主题颜色抽样，不是完整 WCAG 认证。

## 浏览器交互与响应式

- 角速度切换、滑动平均筛选、统计展开正常，数字和单位更新。
- 备用传感器显示空状态并清空旧读数；切回后恢复，方向键旋转与重置正常。
- 设备页打开绑定弹窗、取消关闭正常；个人资料表单、密码表单配色完整。
- 历史页切换 7 天、显示统计并展开明细正常，缺记录与已记录零步仍区分。
- 账户菜单、退出、登录/注册页切换、重新进入演示正常；成功提示已检查为青绿。
- 手机测试 viewport 390 × 844，document scrollWidth 375，不存在页面横向溢出。监测截图 design/sage-mobile.jpg 为浏览器返回的 375 × 812 像素；登录截图 design/sage-login-mobile.jpg 为 390 × 844。窄屏没有源图，仅检查响应式可用性，不进行像素级保真评分。
- 补充证据：design/sage-device-dialog.jpg、design/sage-profile.jpg、design/sage-history.jpg、design/sage-empty.jpg。
- 最终演示页 JavaScript error 日志为空，临时 viewport 覆盖已恢复，预览保持打开。

## Implementation Checklist

- [x] 当前选择图片保存到项目并直接检查。
- [x] 整站颜色集中为主题变量，原有交互与 API 逻辑保留。
- [x] 图表、图例、三维背景与辅助轴同步主题。
- [x] 同尺寸整页及局部对比，修复后再次捕获。
- [x] 检查浅色表单、弹窗、菜单、空状态、成功和错误配色。
- [x] 浏览器交互与手机布局检查。
- [x] npm test：13 项通过；npm run build：通过。
- [x] 工作区没有新分支或临时辅助脚本；保留截图均为验收证据。

## Open Questions / Follow-up Polish

当前范围无需额外选择。真实后端未运行，真实账户与硬件姿态方向仍待联调，本次配色变更没有扩大这部分验证结论。Three.js 与图表包仍有大于 500 kB 的构建体积提示，属于已有性能优化项。字体抗锯齿、模型阴影和网格细节可在后续继续微调。

final result: passed
