# CIVL3704 Transit Map

面向悉尼市中心的公交、Sydney Trains、Sydney Metro 和轻轨地图：显示实时车辆位置、下一站预测延误、数据更新时间和对应的计划线路。课堂投影仍可用 `P` 键切换。

## 运行

在本目录执行：

```powershell
python src/visualization.py --demo
```

演示模式包含四种交通方式，不需要 API Key。浏览器访问 `http://127.0.0.1:8080`。

使用真实数据时，保持 `.env` 中的 `TFNSW_API_KEY`，并在两个终端分别运行：

```powershell
python src/pipeline.py
```

```powershell
python src/visualization.py
```

`data/static/` 放 [TfNSW Timetables Complete GTFS](https://opendata.transport.nsw.gov.au/dataset/timetables-complete-gtfs) 解压后的文件，供公交、轻轨静态线路和站点使用。流水线首次运行时会从 TfNSW 下载 Sydney Trains 专用时刻表到 `data/static_train/`，Metro 专用时刻表到 `data/static_metro/`，以后每 24 小时尝试更新。独立包的 `trip_id` 用于匹配相应的实时数据；完整包的铁路班次 ID 在实测中无法匹配。若自动下载失败，可分别运行 `python src/train_static.py` 或 `python src/train_static.py --metro`，并检查 API Key 是否获得 [Timetables for Realtime](https://opendata.transport.nsw.gov.au/data/dataset/public-transport-timetables-realtime) 及 [v2](https://opendata.transport.nsw.gov.au/data/dataset/public-transport-timetables-realtime-v2) 权限。

流水线读取公交、Sydney Trains、Sydney Metro、L1 Inner West、L2/L3 CBD and South East、Parramatta Light Rail 的 Vehicle Positions 与 Trip Updates。地图范围由 `map_area.json` 决定（见下文“地图范围与投影校准”），Parramatta L4 不在该范围内。部分 feed 失败时其余 feed 继续更新，`data/feed_status.json` 和页面会标示失败来源。TfNSW 应用需要开通相应的 [实时位置](https://opendata.transport.nsw.gov.au/dataset/public-transport-realtime-vehicle-positions)、[v2 实时位置](https://opendata.transport.nsw.gov.au/dataset/public-transport-realtime-vehicle-positions-v2)、[Trip Updates](https://opendata.transport.nsw.gov.au/dataset/public-transport-realtime-trip-update) 与 [v2 Trip Updates](https://opendata.transport.nsw.gov.au/dataset/public-transport-realtime-trip-update-v2) 数据集权限。

合并后的最新数据在 `data/central_sydney_vehicles_with_delay.csv`，网页使用 `/api/vehicles`。旧 `/api/buses` 仍返回公交车辆。路线索引在 `data/journey_routes.sqlite`，轨迹索引在 `data/shape_offsets.json`、`data/train_shape_offsets.json` 和 `data/metro_shape_offsets.json`；静态文件更新后索引会重建。

## 起终点搜索

在 **Find a direct route** 选择交通方式，输入起终点的区域、地标或附近街道（例如 Broadway、Town Hall），也可以点击 **Choose … on map** 后在地图上选点。公交检查 250 米内站点，轻轨 350 米，铁路和 Metro 500 米；搜索 GTFS `stop_times.txt` 中无需换乘的线路，并显示推荐的上下车站与直线距离。点击有到站预测的车辆可在地图上定位。搜索结果来自静态时刻表，未按当天运营日过滤，因此不表示该线路此刻一定有车；步行距离不是实际步行路线。

“即将到站”只显示地图范围内、位置与 Trip Updates 均在 120 秒内更新、且未来 60 分钟内预计到达所示上车站的车辆。铁路和 Metro Trip Updates 有时仅提供延误秒数，程序会结合专用静态时刻表推算预计时间；若缺少匹配的班次或站点则不显示预测。数据过期、演示模式或没有预测时会明确显示没有实时到站信息。

## 地图操作

- 顶部 **Show on map** 筛选栏可直接选择 All、Bus、Train、Metro 或 Light rail。公交车辆显示为圆点，Train、Metro 和轻轨显示为小长方形；填充颜色仍表示下一站预测延误，边框颜色区分交通方式。也可按线路或当前车辆的下一站筛选；选择线路后，下一站菜单只列出该线路当前车辆上报的下一站。没有车辆同时符合筛选条件时，页面会说明原因，并保留所选站点标记与线路轨迹。
- 选择线路可显示当前车辆所属班次的不同**计划轨迹**；车辆点位是上报位置，班次开始前驶向首站时可能落在计划轨迹之外。
- 可显示当前车辆的下一站点位，地图支持缩放和拖动。车辆超过 150 辆时，车号标签在缩放到 15 级及以上才常驻显示，较远视野下鼠标悬停可查看。
- 拖动控制面板顶部可移动面板（鼠标或触屏）；面板位置会保存在浏览器中。顶部获得键盘焦点后可用方向键移动，按住 Shift 可加大步长；底部按钮可恢复默认位置。
- `P` 切换投影界面，`C` 在投影界面中进入校准，`L` 切换车号标签，`B` 切换底图，`F` 切换全屏，`R` 立即刷新。

## 地图范围与投影校准

`map_area.json` 保存地图的南、西、北、东边界，流水线、路线索引和网页共用这一份配置。当前值（纬度 -33.909 至 -33.814，经度 151.106 至 151.271）是根据悉尼港地形模型的照片估计的，大致覆盖 Drummoyne 到 Rose Bay、Lane Cove 到 Redfern。

投影到实体模型上时：按 `F` 全屏，按 `P` 进入投影界面（视野锁定在 `map_area.json` 的范围，不能缩放拖动），再按 `C` 进入校准。可以拖动地图或用方向键移动（按住 Shift 加大步长），用滚轮或 `+`/`-` 缩放，`[`/`]` 微调；红色虚线是当前保存的范围。对齐后按 `Enter`，当前投影画面的范围会写入 `map_area.json`；按 `Esc` 取消。保存只接受本机（127.0.0.1）的请求。正在运行的 `python src/pipeline.py` 会在下一轮（约 60 秒内）按新范围采集车辆，无需重启；路线索引会在首次搜索时自动重建一次，需要几分钟。

## 延误与数据可信度

默认模式参考 [TfNSW Greater Sydney Bus Contract 的首站／中途站离站时间窗口](https://www.transport.nsw.gov.au/system/files/media/documents/2023/Schedule%204%20%28Key%20Performance%20Indicators%29.pdf)：提前达到 1 分钟判为提前，迟到达到 6 分钟判为迟到。地图把这个窗口应用到**下一站预测延误**；铁路、Metro 和轻轨沿用相同颜色阈值仅便于比较，不代表其官方准点标准。颜色由本项目定义，不代表政府官方配色。

“My settings” 模式可调整提前、迟到的分钟阈值和颜色；设置保存在当前浏览器。原始预测秒数仍保留在 CSV 和 `/api/vehicles` 中。

车辆 GPS 或整份快照超过 120 秒未更新时，车点显示为灰色。某个 feed 暂时不返回市中心车辆时，程序最多保留其最近 10 分钟的位置并标为灰色旧位置；超过 10 分钟则隐藏。旧位置不参与实时到站预测。GTFS-R 如提供 `STOPPED_AT` 状态，弹窗会显示“feed reported”；仅在车辆距离下一站 50 米内时，弹窗显示“near next stop”，**不代表确认停车或上下客**。没有站台附近记录也不能单独证明车辆越站。

## 原代码备份

GitHub 的旧提交 `2e3df6b` 保留了修改前已提交的代码。本机另在同级目录 `CIVL3704-Bus-AR-original-20261013/` 保存了修改前的 `src/`、`web/`、`README.md`、`requirements.txt` 和 `.gitignore`，包括当时未提交的地图边界与操作设置。此本机备份不会上传到 GitHub，也不包含 API Key 或大体量数据文件。
