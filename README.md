# ScaleTemp — HX711 + CZL611N + Orange Pi Zero 3 电子秤系统

ScaleTemp 是一个以 **C 底层采集 + Python 中间处理 + FastAPI 网页应用** 为核心的电子秤项目。底层 C 程序只负责 HX711 原始 ADC 采样，Python 负责滤波、稳定性检测、三阶/分段重叠拟合、实验工作流、绘图和 Web API。

## 功能概览

- **底层采集**：`native/hx711_sampler.c` 使用 C 读取 HX711，输出 `unix_time_ns,sequence,raw_adc,status`。
- **中间处理**：默认三阶拟合；校准点少于 4 个时使用 `n-1` 阶；点数大于等于 4 时支持相邻 4 点重叠三阶拟合并对重叠预测取平均。
- **现代黑色主题 Web 仪表盘**：实时重量、原始/滤波曲线、原始到克重转换、stable/unstable、校准点保存、滤波强度调节、中文/英文切换、去皮、刷新。
- **实验数据测算入口**：独立页面引导用户进行 Calibration、Filtering、Dynamic Response、Repeatability、Creep/Drift、Auto-zero 实验。
- **自动输出**：原始 CSV、处理 CSV、PNG/PDF 科研风格图像、JSON 日志。

## 目录结构

```text
native/                         # C 采样器
src/scaletemp/hardware/          # Python 对 C 采样器的进程封装
src/scaletemp/processing/         # 校准、滤波、绘图、实时服务
src/scaletemp/experiments/        # CLI/Web 共用实验工作流
src/scaletemp/web/                # FastAPI + HTML/CSS/JS
scripts/install.sh                # 一键安装
scripts/start_web.sh              # 一键启动 Web
scripts/run_experiments.sh         # 一键启动命令行实验助手
data/raw_data/                    # 自动保存原始实验数据
data/processed_data/              # 自动保存处理后数据
data/figures/                     # 自动保存 PNG/PDF 图像
data/calibration/                 # 校准模型
data/logs/                        # 实验元数据日志
```

## 一键安装

```bash
./scripts/install.sh
```

该命令会创建 `.venv`、安装依赖并编译 C 采样器。

## 一键启动

### Web 仪表盘

```bash
./scripts/start_web.sh
```

默认启动使用 mock 数据，便于无硬件开发；真实 HX711 请使用下面的 `--hardware` 参数。

浏览器打开：

```text
http://<Orange-Pi-IP>:8000
```

同一 WiFi/局域网下的电脑或手机可访问该地址。

### 命令行实验助手

```bash
./scripts/run_experiments.sh
```

菜单流程：

```text
[1] Calibration
[2] Filtering Test
[3] Dynamic Response Test
[4] Repeatability Test
[5] Creep/Drift Test
[6] Auto-zero Test
[7] Generate Final Report Figures
```

## 硬件运行配置

默认启动即使用真实 HX711 硬件，接口与最初的 wiringPi C 程序一致：

- `DT/DOUT = wiringPi 5`
- `SCK/PD_SCK = wiringPi 1`
- `GAIN_PULSES = 1`（A 通道 128 增益）

因此一键启动真实硬件：

```bash
./scripts/start_web.sh
```

如果要显式指定同一组 wiringPi 引脚：

```bash
./scripts/start_web.sh --pins 5 1 1
```

模拟数据必须显式指定：

```bash
./scripts/start_web.sh --mock
```

如需改用 Linux sysfs GPIO 编号而不是 wiringPi 编号：

```bash
./scripts/start_web.sh --sysfs <DOUT_GPIO_NUMBER> <PD_SCK_GPIO_NUMBER> 1
```

> `--pins` 使用 wiringPi 编号，匹配原始代码里的 `wiringPiSetup()`、`#define DT 5`、`#define SCK 1`。`--sysfs` 才使用 Linux GPIO 编号。启动后右侧状态卡显示 `Sensor: wiringpi` 表示真实 wiringPi 硬件后端；显示 `Sensor: mock` 才是模拟模式。

## Web 使用说明

1. 点击 **去皮**：当前空载原始 ADC 均值保存为零点偏移。
2. 在 **校准** 卡片中输入当前砝码克重，点击 **保存校准点**。
3. 调节 **滤波强度**：左侧偏快速响应，右侧偏平滑。
4. 点击 **实验数据测算** 进入独立实验工作流页面。
 5. 实验结束后页面会显示 CSV、PNG、PDF 下载链接。

## 实验工作流说明

- **Calibration**：按质量列表逐个放置标准砝码，自动平均稳定读数，生成原始 ADC vs 重量、拟合对比、阶数 vs RMSE、残差图。
- **Filtering Test**：稳定载荷下采集噪声，比较 Moving Average、Median、EMA，并生成噪声 STD 柱状图。
- **Dynamic Response Test**：引导快速加载/卸载，计算 rise time、settling time、overshoot、time constant、dynamic error。
- **Repeatability Test**：多次放置同一载荷，生成重复性散点/统计图。
- **Creep/Drift Test**：恒定载荷长时间记录，生成长期漂移和蠕变图。
- **Auto-zero Test**：移除载荷后记录回零过程。

## 开发与测试

```bash
make build
PYTHONPATH=src python -m pytest -q
```

## 采样层约束

C 采样器只输出原始 ADC，不做去皮、校准、滤波或重量换算，确保底层职责单一并尽量接近最高采样速率。所有后续处理均在 Python 中间层完成。
