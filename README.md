# CIVL3704 Bus Map

面向悉尼市中心公交乘客的课程项目：显示实时车辆位置、下一站预测延误、数据更新时间和对应的计划线路。课堂投影仍可用 `P` 键切换。

## 运行

在本目录执行：

```powershell
python src/visualization.py --demo
```

演示模式不需要 API Key。浏览器访问 `http://127.0.0.1:8080`。

使用真实数据时，保持原有 `.env` 中的 `TFNSW_API_KEY`，并在两个终端分别运行：

```powershell
python src/pipeline.py
```

```powershell
python src/visualization.py
```

`data/static/` 下的 `routes.txt`、`trips.txt` 用于线路匹配；`stops.txt` 用于显示站名和下一站位置；`shapes.txt` 用于选定线路的计划轨迹。第一次选择线路时，程序会扫描较大的 `shapes.txt` 并在 `data/shape_offsets.json` 生成索引。静态文件更新后重启显示程序，索引会自动重建。

## 地图操作

- 按线路或下一站筛选当前车辆。
- 选择线路可显示其中一趟车的**计划轨迹**；同一线路的其他班次可能走不同路径。
- 可显示当前车辆的下一站点位，地图支持缩放和拖动。数据采集仍只覆盖市中心区域（纬度 -33.90 至 -33.84，经度 151.18 至 151.23）。
- `P` 切换投影界面，`L` 切换车号标签，`B` 切换底图，`F` 切换全屏，`R` 立即刷新。

## 延误与数据可信度

默认模式参考 [TfNSW Greater Sydney Bus Contract 的首站／中途站离站时间窗口](https://www.transport.nsw.gov.au/system/files/media/documents/2023/Schedule%204%20%28Key%20Performance%20Indicators%29.pdf)：提前达到 1 分钟判为提前，迟到达到 6 分钟判为迟到。地图把这个窗口应用到**下一站预测延误**，因此它不是 TfNSW 的实际准点考核结果。颜色由本项目定义，不代表政府官方配色。

“My settings” 模式可调整提前、迟到的分钟阈值和颜色；设置保存在当前浏览器。原始预测秒数仍保留在 CSV 和 `/api/buses` 中。

车辆 GPS 或整份快照超过 120 秒未更新时，车点显示为灰色。GTFS-R 如提供 `STOPPED_AT` 状态，弹窗会显示“feed reported”；仅在车辆距离下一站 50 米内时，弹窗显示“near next stop”，**不代表确认停车或上下客**。没有站台附近记录也不能单独证明车辆越站。

## 原代码备份

GitHub 的旧提交 `2e3df6b` 保留了修改前已提交的代码。本机另在同级目录 `CIVL3704-Bus-AR-original-20261013/` 保存了修改前的 `src/`、`web/`、`README.md`、`requirements.txt` 和 `.gitignore`，包括当时未提交的地图边界与操作设置。此本机备份不会上传到 GitHub，也不包含 API Key 或大体量数据文件。
