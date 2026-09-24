/**
 * 图表工具前端逻辑（改版 v2：分类 + 全量风格变体画廊 + 顶部筛选条 + 每张图复制/下载）
 * - 顶部 4 个「大类」新拟态标签：柱形类 / 折线类 / 饼环类 / 关系分布类
 * - 每个大类下挂几十种「风格变体」（形状/配色/排列 三维度），点大类下方网格全部渲染
 * - 网格上方「筛选条」按维度过滤（形状/配色/排列 等），可多选并集
 * - 每张图表独立 ECharts 实例，右上角工具条：下载 PNG / 复制图片 / 下载数据 CSV / 全屏
 * - X/Y 选择面板作为所有卡片统一数据源；自适应 + 主题切换自动重绘
 */
document.addEventListener('DOMContentLoaded', function () {
    // ---------- DOM ----------
    const uploadBtn   = document.getElementById('uploadBtn');
    const catBar      = document.getElementById('catBar');
    const filterBar   = document.getElementById('filterBar');
    const subList     = document.getElementById('subList');
    const previewPane = document.getElementById('previewPane');
    const previewTitle= document.getElementById('previewTitle');
    const previewTools= document.getElementById('previewTools');
    const labelToggle = document.getElementById('labelToggle');
    const tableToggle = document.getElementById('tableToggle');
    const previewCanvas=document.getElementById('previewCanvas');
    const mainTable   = document.getElementById('mainTable');
    const chartEmpty  = document.getElementById('chartEmpty');
    const xAxisSelect = document.getElementById('xAxisSelect');
    const yChips      = document.getElementById('yChips');
    const previewBox  = document.getElementById('previewBox');
    const fileNameEl  = document.getElementById('fileName');
    const configPanel = document.getElementById('configPanel');
    const configToggle= document.getElementById('configToggle');

    const modal      = document.getElementById('uploadModal');
    const dropZone   = document.getElementById('dropZone');
    const pickFile   = document.getElementById('pickFile');
    const fileInput  = document.getElementById('fileInput');
    const dropInfo   = document.getElementById('dropInfo');
    const cancelBtn  = document.getElementById('cancelUpload');
    const confirmBtn = document.getElementById('confirmUpload');

    // ---------- 调色板 ----------
    const BASE_PALETTE = ['#2f6df6', '#16a34a', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4', '#ec4899', '#64748b'];
    function getThemePalette() {
        // 5 套明亮主题 + 黑夜模式都跟随展厅灯光主题色（data-light-theme）作为第一色，其余保持鲜明色板
        const themePrimary = (cssVar('--theme-primary') || '').trim();
        if (themePrimary) {
            // silver 主题主色为浅银灰（#e2e8f0），直接作图表首色在浅背景上几乎不可见，改用更深的 slate 保证可读
            const base = (document.body.getAttribute('data-light-theme') === 'silver')
                ? '#94a3b8'
                : themePrimary;
            return [base, '#16a34a', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4', '#ec4899', '#64748b'];
        }
        return BASE_PALETTE;
    }

    // ---------- 变体注册表（数据驱动，支撑筛选与渲染） ----------
    // kind: 决定 build 逻辑族（bar/line/pie/scatter/radar/funnel/treemap）
    // tag : 当前大类的维度标签（用于筛选条），每类一套
    const VARIANTS = {
        bar: [
            // 基础形状
            { id:'bar-basic',    name:'基础柱状图',   kind:'bar', group:'基础形状', shape:'basic' },
            { id:'bar-round',    name:'圆角柱状图',   kind:'bar', group:'基础形状', shape:'round' },
            { id:'bar-topround', name:'顶部圆角柱',   kind:'bar', group:'基础形状', shape:'topround' },
            { id:'bar-capsule',  name:'胶囊柱状图',   kind:'bar', group:'基础形状', shape:'capsule' },
            { id:'bar-cylinder', name:'圆柱柱状图',   kind:'bar', group:'基础形状', shape:'cylinder' },
            { id:'bar-border',   name:'描边柱状图',   kind:'bar', group:'基础形状', shape:'border' },
            { id:'bar-shadow',   name:'阴影柱状图',   kind:'bar', group:'基础形状', shape:'shadow' },
            { id:'bar-stripe',   name:'条纹纹理柱',   kind:'bar', group:'基础形状', shape:'stripe' },
            { id:'bar-hollow',   name:'空心描边柱',   kind:'bar', group:'基础形状', shape:'hollow' },
            { id:'bar-mark',     name:'极值标记柱',   kind:'bar', group:'基础形状', shape:'markpoint' },
            { id:'bar-bg',       name:'背景底柱',     kind:'bar', group:'基础形状', shape:'bg' },
            // 创意形状
            { id:'bar-water',    name:'水滴柱状图',   kind:'bar', group:'创意形状', shape:'water' },
            { id:'bar-taper',    name:'锥形柱状图',   kind:'bar', group:'创意形状', shape:'taper' },
            { id:'bar-triangle', name:'三角柱状图',   kind:'bar', group:'创意形状', shape:'triangle' },
            { id:'bar-peak',     name:'山峰柱状图',   kind:'bar', group:'创意形状', shape:'peak' },
            { id:'bar-diamond',  name:'菱形柱状图',   kind:'bar', group:'创意形状', shape:'diamond' },
            { id:'bar-3d',       name:'立体棱柱图',   kind:'bar', group:'创意形状', shape:'box3d' },
            { id:'bar-arrow',    name:'箭头柱状图',   kind:'bar', group:'创意形状', shape:'arrow' },
            { id:'bar-lollipop', name:'棒棒糖柱',     kind:'bar', group:'创意形状', shape:'lollipop' },
            { id:'bar-pictorial',name:'象形柱状图',   kind:'pictorial', group:'创意形状' },
            // 配色
            { id:'bar-vgrad',    name:'竖向渐变柱',   kind:'bar', group:'配色', palette:'vgrad' },
            { id:'bar-rgrad',    name:'径向渐变柱',   kind:'bar', group:'配色', palette:'rgrad' },
            { id:'bar-multicolor',name:'逐类异色柱',  kind:'bar', group:'配色', palette:'perclass' },
            { id:'bar-bgcolor',  name:'柱底背景对比', kind:'bar', group:'配色', palette:'bgcolor' },
            { id:'bar-alpha',    name:'半透明柱',     kind:'bar', group:'配色', palette:'alpha' },
            { id:'bar-highlight',name:'高亮选中柱',   kind:'bar', group:'配色', palette:'highlight' },
            { id:'bar-thresh',   name:'双色阈值柱',   kind:'bar', group:'配色', palette:'threshold' },
            { id:'bar-stripe-c', name:'斜纹纹理柱',   kind:'bar', group:'配色', palette:'stripe' },
            // 排列
            { id:'bar-stack',    name:'堆叠柱状图',   kind:'bar', group:'排列', arrange:'stack' },
            { id:'bar-stackr',   name:'圆角堆叠柱',   kind:'bar', group:'排列', arrange:'stack', shape:'round' },
            { id:'bar-pct',      name:'百分比堆叠柱', kind:'bar', group:'排列', arrange:'pct' },
            { id:'bar-posneg',   name:'正负柱状图',   kind:'bar', group:'排列', arrange:'posneg' },
            { id:'bar-polar',    name:'极坐标柱状图', kind:'bar', group:'排列', arrange:'polar' },
            { id:'bar-h',        name:'横向条形图',   kind:'bar', group:'排列', arrange:'horizontal' },
            { id:'bar-target',   name:'目标达成柱',   kind:'bar', group:'排列', arrange:'target' }
        ],
        line: [
            // 线型
            { id:'line-basic',   name:'基础折线图',   kind:'line', group:'线型' },
            { id:'line-smooth',  name:'平滑折线图',   kind:'line', group:'线型', smooth:true },
            { id:'line-step',    name:'阶梯折线图',   kind:'line', group:'线型', step:'middle' },
            { id:'line-step-s',  name:'起点阶梯线',   kind:'line', group:'线型', step:'start' },
            { id:'line-step-e',  name:'终点阶梯线',   kind:'line', group:'线型', step:'end' },
            { id:'line-dashed',  name:'虚线折线图',   kind:'line', group:'线型', dash:'dashed' },
            { id:'line-dotted',  name:'点线折线图',   kind:'line', group:'线型', dash:'dotted' },
            { id:'line-symbol',  name:'标记点折线',   kind:'line', group:'线型', symbol:'circle' },
            { id:'line-thick',   name:'粗线折线图',   kind:'line', group:'线型', thick:true },
            { id:'line-grad',    name:'渐变线折线',   kind:'line', group:'线型', lineGrad:true },
            // 面积
            { id:'line-area',     name:'面积图',       kind:'line', group:'面积', area:true },
            { id:'line-areagrad', name:'渐变面积图',   kind:'line', group:'面积', area:true, areaGrad:true },
            { id:'line-stackarea',name:'堆叠面积图',   kind:'line', group:'面积', area:true, stack:true },
            { id:'line-pctarea',  name:'百分比堆叠面积',kind:'line',group:'面积', area:true, pct:true },
            { id:'line-biarea',   name:'双向面积图',   kind:'line', group:'面积', area:true, bi:true },
            { id:'line-polar',    name:'极坐标折线',   kind:'line', group:'面积', polar:true },
            { id:'line-zoom',     name:'区域缩略折线', kind:'line', group:'面积', zoom:true },
            // 标注
            { id:'line-markmax',  name:'极值标注线',   kind:'line', group:'标注', mark:'max' },
            { id:'line-markavg',  name:'均值参考线',   kind:'line', group:'标注', mark:'avg' },
            { id:'line-markarea', name:'区间高亮线',   kind:'line', group:'标注', mark:'area' }
        ],
        pie: [
            // 形态
            { id:'pie-basic',  name:'基础饼图',     kind:'pie', group:'形态' },
            { id:'pie-dough',  name:'环形图',       kind:'pie', group:'形态', doughnut:true },
            { id:'pie-half',   name:'半圆环图',     kind:'pie', group:'形态', doughnut:true, half:true },
            { id:'pie-rose-r', name:'南丁格尔玫瑰(半径)', kind:'pie', group:'形态', rose:'radius' },
            { id:'pie-rose-a', name:'南丁格尔玫瑰(面积)', kind:'pie', group:'形态', rose:'area' },
            { id:'pie-nested', name:'嵌套环形图',   kind:'pie', group:'形态', nested:true },
            // 样式
            { id:'pie-grad',   name:'渐变扇区饼',   kind:'pie', group:'样式', pieGrad:true },
            { id:'pie-border', name:'描边扇区饼',   kind:'pie', group:'样式', pieBorder:true },
            { id:'pie-round',  name:'圆角扇区饼',   kind:'pie', group:'样式', pieRound:true },
            { id:'pie-shadow', name:'阴影扇区饼',   kind:'pie', group:'样式', pieShadow:true },
            { id:'pie-explode',name:'突出扇区饼',   kind:'pie', group:'样式', pieExplode:true },
            // 标签
            { id:'pie-pct',    name:'百分比标签饼', kind:'pie', group:'标签', pctLabel:true },
            { id:'pie-center', name:'中心标题饼',   kind:'pie', group:'标签', centerTitle:true },
            { id:'pie-legend', name:'图例滚动饼',   kind:'pie', group:'标签', legendScroll:true }
        ],
        rel: [
            // 散点
            { id:'sc-basic',   name:'基础散点图',   kind:'scatter', group:'散点' },
            { id:'sc-bubble',  name:'气泡散点图',   kind:'scatter', group:'散点', bubble:true },
            { id:'sc-grad',    name:'渐变散点图',   kind:'scatter', group:'散点', scGrad:true },
            { id:'sc-effect',  name:'涟漪散点图',   kind:'scatter', group:'散点', effect:true },
            { id:'sc-large',   name:'大数据散点',   kind:'scatter', group:'散点', large:true },
            { id:'sc-trend',   name:'趋势拟合散点', kind:'scatter', group:'散点', trend:true },
            { id:'sc-symbol',  name:'菱形符号散点', kind:'scatter', group:'散点', scSymbol:'diamond' },
            { id:'sc-shape',   name:'多符号散点',   kind:'scatter', group:'散点', scMultiSymbol:true },
            // 雷达
            { id:'radar-basic', name:'基础雷达图',   kind:'radar', group:'雷达' },
            { id:'radar-area',  name:'面积雷达图',   kind:'radar', group:'雷达', radarArea:true },
            { id:'radar-multi', name:'多指标雷达',   kind:'radar', group:'雷达', radarMulti:true },
            // 漏斗
            { id:'fun-basic', name:'基础漏斗图',   kind:'funnel', group:'漏斗' },
            { id:'fun-grad',  name:'渐变漏斗图',   kind:'funnel', group:'漏斗', funGrad:true },
            { id:'fun-label', name:'内部标签漏斗', kind:'funnel', group:'漏斗', funLabel:true },
            // 树图
            { id:'tree-basic',  name:'基础矩形树图', kind:'treemap', group:'树图' },
            { id:'tree-multi',  name:'多层级树图',   kind:'treemap', group:'树图', treeMulti:true },
            { id:'tree-border', name:'描边矩形树图', kind:'treemap', group:'树图', treeBorder:true },
            // 关系网络
            { id:'rel-graph',   name:'关系网络图',   kind:'graph', group:'关系网络' },
            { id:'rel-network', name:'力导向关系图', kind:'graph', group:'关系网络', force:true }
        ],
        stat: [
            // 统计科研（2D）
            { id:'stat-waterfall', name:'瀑布图',     kind:'stat', group:'统计', stat:'waterfall' },
            { id:'stat-stem',      name:'棒棒糖茎图', kind:'stat', group:'统计', stat:'stem' },
            { id:'stat-errorbar',  name:'误差棒图',   kind:'stat', group:'统计', stat:'errorbar' },
            { id:'stat-boxplot',   name:'箱线图',     kind:'stat', group:'统计', stat:'boxplot' },
            { id:'stat-heatmap',   name:'热力图',     kind:'stat', group:'统计', stat:'heatmap' },
            { id:'stat-parallel',  name:'平行坐标图', kind:'stat', group:'统计', stat:'parallel' },
            { id:'stat-gauge',     name:'仪表盘',     kind:'stat', group:'统计', stat:'gauge' },
            { id:'stat-sankey',    name:'桑基图',     kind:'stat', group:'统计', stat:'sankey' },
            { id:'stat-sunburst',  name:'旭日图',     kind:'stat', group:'统计', stat:'sunburst' },
            { id:'stat-histogram', name:'直方图',     kind:'histogram', group:'统计' },
            { id:'stat-pareto',    name:'帕累托图',   kind:'pareto', group:'统计' },
            { id:'stat-pictorial', name:'象形对比柱', kind:'pictorial', group:'统计' },
            // 关系/时序
            { id:'stat-graph',     name:'关系网络图', kind:'graph', group:'关系网络' },
            { id:'stat-calendar',  name:'日历热力图', kind:'calendar', group:'关系网络' },
            { id:'stat-themeriver',name:'主题河流图', kind:'themeriver', group:'关系网络' },
            // 3D（依赖 echarts-gl）
            { id:'stat-bar3d',     name:'3D 柱状图',  kind:'stat', group:'3D', stat:'bar3d' },
            { id:'stat-surface',   name:'3D 曲面图',  kind:'stat', group:'3D', stat:'surface' }
        ]
    };
    const TYPE_NAMES = {};
    Object.values(VARIANTS).forEach(list => list.forEach(v => { TYPE_NAMES[v.id] = v.name; }));

    // ---------- 状态 ----------
    let chartData = null;
    let currentCat = 'bar';
    let pendingFile = null;
    let activeTags = new Set();           // 筛选条当前选中的维度标签（空=全部）
    let selectedVariantId = null;         // 当前主预览选中的变体
    let showLabels = false;               // 数据标签开关（数值嵌入图形）
    let showTable = true;                 // 迷你数据表开关
    const thumbInst = new Map();          // variant.id -> 缩略图实例（左侧列表）
    let mainInst = null;                  // 主预览实例（右侧大图）

    // ---------- 工具 ----------
    function cssVar(name) {
        const el = document.querySelector('.chart-page');
        if (el) {
            const v = getComputedStyle(el).getPropertyValue(name).trim();
            if (v) return v;
        }
        return (getComputedStyle(document.documentElement).getPropertyValue(name) ||
                getComputedStyle(document.body).getPropertyValue(name)).trim();
    }
    function selectedY() {
        return Array.from(yChips.querySelectorAll('.ct-ychip.on')).map(b => b.dataset.col);
    }
    function showEmpty(msg) {
        subList.style.display = 'none';
        previewPane.style.display = 'none';
        filterBar.style.display = 'none';
        chartEmpty.querySelector('div:nth-child(2)').textContent = msg ||
            '请先上传 Excel / CSV 文件，选择列后自动生成图表';
        chartEmpty.style.display = 'flex';
    }
    function hideEmpty() {
        chartEmpty.style.display = 'none';
        subList.style.display = '';
        previewPane.style.display = '';
        filterBar.style.display = '';
    }
    function toast(msg) { alert(msg); }
    function hexToRgb(hex) {
        hex = (hex || '#2f6df6').replace('#', '');
        if (hex.length === 3) hex = hex.split('').map(c => c + c).join('');
        const n = parseInt(hex, 16);
        return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
    }
    function rgba(hex, a) { const [r,g,b] = hexToRgb(hex); return `rgba(${r},${g},${b},${a})`; }
    function lighten(hex, amt) {
        const [r,g,b] = hexToRgb(hex);
        const f = c => Math.round(c + (255 - c) * amt);
        return `rgb(${f(r)},${f(g)},${f(b)})`;
    }

    // ---------- 颜色渐变辅助 ----------
    function vGrad(color) {
        return new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: lighten(color, 0.15) },
            { offset: 1, color: rgba(color, 0.55) }
        ]);
    }
    function rGrad(color) {
        return new echarts.graphic.RadialGradient(0.5, 0.5, 0.85, [
            { offset: 0, color: lighten(color, 0.2) },
            { offset: 1, color: rgba(color, 0.45) }
        ]);
    }
    function stripeGrad(color) {
        const stops = [];
        for (let i = 0; i <= 10; i++) {
            const c = (i % 2 === 0) ? color : rgba(color, 0.4);
            stops.push({ offset: i / 10, color: c });
        }
        return new echarts.graphic.LinearGradient(0, 0, 1, 0, stops);
    }

    // ---------- 坐标轴/图例基础 ----------
    function catAxis(data, name, tc, ac) {
        return { type:'category', data, name, axisLabel:{ color: tc }, axisLine:{ lineStyle:{ color: ac } }, nameTextStyle:{ color: tc } };
    }
    function valAxis(tc, ac) {
        return { type:'value', axisLabel:{ color: tc }, axisLine:{ lineStyle:{ color: ac } },
            splitLine:{ lineStyle:{ color: ac, opacity:.35 } }, nameTextStyle:{ color: tc } };
    }
    function gridDef() { return { left: 50, right: 30, top: 52, bottom: 56, containLabel: true }; }
    function legendDef(names, tc) { return { data: names, textStyle:{ color: tc }, top: 10, type:'scroll' }; }

    // ---------- 聚合（饼/漏斗/树图按 x 汇总） ----------
    function aggByX(xData, ySeries) {
        return xData.map((label, i) => {
            const vals = ySeries.map(s => s.data[i] || 0);
            return { label, vals, total: vals.reduce((a, b) => a + b, 0) };
        });
    }

    // ---------- 自定义柱形形状（锥形/水滴/三角/山峰/立体/箭头/棒棒糖） ----------
    const CUSTOM_SHAPES = ['taper','water','triangle','peak','box3d','arrow','lollipop','diamond'];
    function customBarRenderItem(shape, seriesCount) {
        return function (params, api) {
            const label = api.value(0);
            const val = api.value(1);
            const pt = api.coord([label, val]);
            const base = api.coord([label, 0]);
            const band = api.size([1, 0])[0];
            const count = seriesCount || 1;
            const step = band / count;
            const w = Math.max(3, Math.min(step * 0.7, 42));
            const slotX = pt[0] - band / 2 + step * (params.seriesIndex + 0.5);
            const x = slotX - w / 2;
            const topY = Math.min(pt[1], base[1]);
            const h = Math.abs(base[1] - pt[1]);
            const color = api.visual('color');
            if (!isFinite(h) || h <= 0) {
                if (shape === 'lollipop') return { type:'circle', shape:{ cx: slotX, cy: base[1], r: Math.min(w, 12) * 0.4 }, style:{ fill: color } };
                return;
            }
            const bottomY = base[1];
            if (shape === 'taper') {
                const tw = w * 0.35;
                return { type:'polygon', shape:{ points:[[x, topY],[x + w, topY],[x + w - tw/2, topY + h],[x + tw/2, topY + h]] }, style:{ fill: color } };
            }
            if (shape === 'water') {
                const bw = w * 0.46;
                return { type:'polygon', shape:{ points:[[x, topY],[x + w, topY],[x + w/2 + bw/2, topY + h],[x + w/2, topY + h + bw/2],[x + w/2 - bw/2, topY + h]] }, style:{ fill: color } };
            }
            if (shape === 'triangle') {
                return { type:'polygon', shape:{ points:[[x, bottomY],[x + w, bottomY],[x + w/2, topY]] }, style:{ fill: color } };
            }
            if (shape === 'peak') {
                const m = w * 0.18;
                return { type:'polygon', shape:{ points:[[x + m, bottomY],[x + w - m, bottomY],[x + w/2, topY]] }, style:{ fill: color } };
            }
            if (shape === 'arrow') {
                const ah = Math.min(w * 1.4, h * 0 + h * 0.4);
                const shaftH = Math.max(0, h - ah);
                return { type:'group', children:[
                    { type:'rect', shape:{ x:x, y:topY + ah, width:w, height:shaftH }, style:{ fill: color } },
                    { type:'polygon', shape:{ points:[[x, topY],[x + w, topY],[x + w/2, topY - ah]] }, style:{ fill: color } }
                ] };
            }
            if (shape === 'box3d') {
                const d = Math.min(w * 0.32, 12);
                const sy = -d * 0.6;
                const sx = d;
                return { type:'group', children:[
                    { type:'rect', shape:{ x:x, y:topY, width:w, height:h }, style:{ fill: color } },
                    { type:'polygon', shape:{ points:[[x, topY],[x + w, topY],[x + w + sx, topY + sy],[x + sx, topY + sy]] }, style:{ fill: color } },
                    { type:'polygon', shape:{ points:[[x, topY],[x + w, topY],[x + w + sx, topY + sy],[x + sx, topY + sy]] }, style:{ fill: 'rgba(255,255,255,0.30)' } },
                    { type:'polygon', shape:{ points:[[x + w, topY],[x + w, topY + h],[x + w + sx, topY + h + sy],[x + w + sx, topY + sy]] }, style:{ fill: color } },
                    { type:'polygon', shape:{ points:[[x + w, topY],[x + w, topY + h],[x + w + sx, topY + h + sy],[x + w + sx, topY + sy]] }, style:{ fill: 'rgba(0,0,0,0.22)' } }
                ] };
            }
            if (shape === 'lollipop') {
                const r = Math.min(w, 12) * 0.45;
                return { type:'group', children:[
                    { type:'rect', shape:{ x: slotX - 1.5, y: topY, width: 3, height: h }, style:{ fill: color } },
                    { type:'circle', shape:{ cx: slotX, cy: topY, r: r }, style:{ fill: color } }
                ] };
            }
            if (shape === 'diamond') {
                const my = topY + h / 2;
                return { type:'polygon', shape:{ points:[[slotX - w/2, my],[slotX, topY],[slotX + w/2, my],[slotX, bottomY]] }, style:{ fill: color } };
            }
            return { type:'rect', shape:{ x:x, y:topY, width:w, height:h }, style:{ fill: color } };
        };
    }

    // 误差棒（custom）：I 形误差线 + 中心圆点
    function errorBarRenderItem(params, api) {
        const label = api.value(0);
        const y = api.value(1);
        const err = api.value(2) || 0;
        const cx = api.coord([label, 0])[0];
        const centerY = api.coord([label, y])[1];
        const topY = api.coord([label, y + err])[1];
        const botY = api.coord([label, y - err])[1];
        const color = api.visual('color') || '#2f6df6';
        const cap = 6;
        return { type:'group', children:[
            { type:'line', shape:{ x1:cx, y1:topY, x2:cx, y2:botY }, style:{ stroke: color, lineWidth: 2 } },
            { type:'line', shape:{ x1:cx - cap, y1:topY, x2:cx + cap, y2:topY }, style:{ stroke: color, lineWidth: 2 } },
            { type:'line', shape:{ x1:cx - cap, y1:botY, x2:cx + cap, y2:botY }, style:{ stroke: color, lineWidth: 2 } },
            { type:'circle', shape:{ cx:cx, cy:centerY, r:3 }, style:{ fill: color } }
        ] };
    }

    // ============ 上传弹窗 ============
    function openModal() {
        pendingFile = null; fileInput.value = ''; dropInfo.style.display = 'none';
        confirmBtn.disabled = true;
        modal.classList.add('show');
    }
    function closeModal() { modal.classList.remove('show'); }

    uploadBtn.addEventListener('click', openModal);
    cancelBtn.addEventListener('click', closeModal);
    modal.addEventListener('click', e => { if (e.target === modal) closeModal(); });
    pickFile.addEventListener('click', e => { e.stopPropagation(); fileInput.click(); });
    dropZone.addEventListener('click', () => fileInput.click());
    dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('dragover'); });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
    dropZone.addEventListener('drop', e => {
        e.preventDefault(); dropZone.classList.remove('dragover');
        if (e.dataTransfer.files.length) handlePicked(e.dataTransfer.files[0]);
    });
    fileInput.addEventListener('change', () => { if (fileInput.files.length) handlePicked(fileInput.files[0]); });

    function handlePicked(file) {
        const ext = '.' + (file.name.split('.').pop() || '').toLowerCase();
        const allowed = ['.xlsx', '.xls', '.csv'];
        if (!allowed.includes(ext)) { toast('不支持的格式，请上传 .xlsx / .xls / .csv'); return; }
        if (file.size > 10 * 1024 * 1024) { toast('文件超过 10MB 限制'); return; }
        pendingFile = file;
        dropInfo.style.display = 'block';
        dropInfo.textContent = '已选择：' + file.name + '（' + (file.size / 1024).toFixed(1) + ' KB）';
        confirmBtn.disabled = false;
    }

    confirmBtn.addEventListener('click', () => {
        if (!pendingFile) return;
        const fd = new FormData();
        fd.append('file', pendingFile);
        confirmBtn.disabled = true;
        confirmBtn.textContent = '上传中…';
        fetch('/chart/upload', { method: 'POST', body: fd })
            .then(r => r.json())
            .then(res => {
                confirmBtn.textContent = '上传并生成';
                if (res.code !== 200) { toast('上传失败：' + (res.msg || '未知错误')); confirmBtn.disabled = false; return; }
                chartData = res.data;
                fileNameEl.textContent = '（' + (chartData.filename || '') + '）';
                populateControls();
                renderPreview();
                closeModal();
                renderLayout();
            })
            .catch(err => { confirmBtn.textContent = '上传并生成'; confirmBtn.disabled = false; toast('上传请求失败：' + err.message); });
    });

    // ============ 控件填充 ============
    function populateControls() {
        const cols = chartData.columns || [];
        const rec = chartData.recommendations || {};
        xAxisSelect.innerHTML = cols.map(c => `<option value="${c}">${c}</option>`).join('');
        xAxisSelect.value = rec.x_col || cols[0] || '';
        let defY = rec.y_col;
        yChips.innerHTML = cols.map(c => `<button type="button" class="ct-ychip" data-col="${c}">${c}</button>`).join('');
        let anyOn = false;
        yChips.querySelectorAll('.ct-ychip').forEach(b => {
            if (b.dataset.col === defY) { b.classList.add('on'); anyOn = true; }
            b.addEventListener('click', () => toggleY(b));
        });
        if (!anyOn && cols[1]) {
            const numCols = rec.numeric_columns || [];
            const target = numCols.length ? numCols[0] : cols[1];
            const el = Array.from(yChips.querySelectorAll('.ct-ychip')).find(x => x.dataset.col === target);
            if (el) el.classList.add('on');
        }
    }

    function toggleY(btn) {
        const onCount = yChips.querySelectorAll('.ct-ychip.on').length;
        if (btn.classList.contains('on')) {
            if (onCount <= 1) { toast('至少保留一个 Y 轴'); return; }
            btn.classList.remove('on');
        } else {
            btn.classList.add('on');
        }
        renderLayout();
    }

    xAxisSelect.addEventListener('change', renderLayout);

    // ============ 当前大类变体（受筛选条影响） ============
    function currentVariants() {
        const list = VARIANTS[currentCat] || [];
        if (activeTags.size === 0) return list;
        return list.filter(v => activeTags.has(v.group));
    }

    // ============ 筛选条（带数量徽章，让用户明确过滤后会剩多少张） ============
    function buildFilterBar() {
        const allList = VARIANTS[currentCat] || [];
        const groups = [];
        allList.forEach(v => { if (!groups.includes(v.group)) groups.push(v.group); });
        filterBar.innerHTML = '';
        const all = document.createElement('button');
        all.type = 'button'; all.className = 'ct-fchip' + (activeTags.size === 0 ? ' active' : '');
        all.textContent = '全部 ' + allList.length; all.dataset.tag = '';
        all.addEventListener('click', () => { activeTags.clear(); syncFilterActive(); applyFilter(); });
        filterBar.appendChild(all);
        groups.forEach(t => {
            const count = allList.filter(v => v.group === t).length;
            const b = document.createElement('button');
            b.type = 'button'; b.className = 'ct-fchip'; b.textContent = t + ' ' + count; b.dataset.tag = t;
            b.addEventListener('click', () => {
                if (activeTags.has(t)) activeTags.delete(t); else activeTags.add(t);
                syncFilterActive(); applyFilter();
            });
            filterBar.appendChild(b);
        });
    }

    // 筛选后仅重建缩略图列表 + 主预览（不重建筛选条，避免递归）
    function applyFilter() {
        if (!currentVariants().some(v => v.id === selectedVariantId)) {
            const first = currentVariants()[0];
            selectedVariantId = first ? first.id : null;
        }
        buildSubList();
        renderMain();
        renderMiniTable();
    }
    function syncFilterActive() {
        filterBar.querySelectorAll('.ct-fchip').forEach(b => {
            const t = b.dataset.tag;
            if (t === '') b.classList.toggle('active', activeTags.size === 0);
            else b.classList.toggle('active', activeTags.has(t));
        });
    }

    // ============ 数据上下文（X/Y 提取，供缩略图/主预览/数据表复用） ============
    function buildCtx() {
        const xKey = xAxisSelect.value;
        const yCols = selectedY();
        const rows = chartData.data || [];
        const xData = rows.map(r => r[xKey]);
        const ySeries = yCols.map(yc => ({
            name: yc,
            data: rows.map(r => {
                let v = r[yc];
                if (typeof v === 'string') v = parseFloat(v);
                return isNaN(v) ? 0 : v;
            })
        }));
        const tc = cssVar('--text-primary') || '#333';
        const ac = cssVar('--border-color') || '#cfd4dc';
        return { xData, ySeries, xName: xKey, yNames: yCols, textColor: tc, axisColor: ac, palette: getThemePalette() };
    }

    // ============ 主布局：左侧缩略图 + 右侧主预览 ============
    function disposeAll() {
        thumbInst.forEach(i => i.dispose()); thumbInst.clear();
        if (mainInst) { mainInst.dispose(); mainInst = null; }
        selectedVariantId = null;
    }

    function renderLayout() {
        buildFilterBar();
        if (!chartData) { showEmpty(); return; }
        const xKey = xAxisSelect.value;
        const yCols = selectedY();
        if (!xKey || !yCols.length) { showEmpty('请选择 X 轴与至少一个 Y 轴'); return; }
        hideEmpty();
        // 选中项不属于当前大类时，重置为当前大类第一个
        const cur = VARIANTS[currentCat] || [];
        if (!selectedVariantId || !cur.some(v => v.id === selectedVariantId)) {
            selectedVariantId = cur.length ? cur[0].id : null;
        }
        buildSubList();
        renderMain();
        renderMiniTable();
    }

    // 为缩略图生成精简数据上下文：只取前 4 个类别 + 第 1 个 Y 列，让图形足够大、可辨识
    function makeThumbCtx(fullCtx) {
        const maxX = 4;
        const xData = fullCtx.xData.slice(0, maxX);
        const ySeries = fullCtx.ySeries.slice(0, 1).map(s => ({ name: s.name, data: s.data.slice(0, maxX) }));
        return Object.assign({}, fullCtx, { xData, ySeries, xName: '', yNames: ySeries.map(s => s.name) });
    }

    // 左侧缩略图列表（每个变体一个小实例，只画图形、不显示数值/坐标轴/标题；按 group 二级分组）
    function buildSubList() {
        thumbInst.forEach(i => i.dispose()); thumbInst.clear();
        subList.innerHTML = '';
        const fullCtx = buildCtx();
        const thumbCtx = makeThumbCtx(fullCtx);
        let lastGroup = null;
        currentVariants().forEach(v => {
            if (v.group !== lastGroup) {
                lastGroup = v.group;
                const h = document.createElement('div');
                h.className = 'ct-group-title';
                h.textContent = v.group;
                subList.appendChild(h);
            }
            const card = document.createElement('div');
            card.className = 'ct-thumb';
            card.dataset.type = v.id;
            card.innerHTML =
                '<div class="ct-thumb-canvas"></div>' +
                '<div class="ct-thumb-title">' + v.name + '</div>';
            card.addEventListener('click', () => {
                selectedVariantId = v.id;
                syncSubListActive();
                renderMain();
                renderMiniTable();
            });
            subList.appendChild(card);
            const canvas = card.querySelector('.ct-thumb-canvas');
            const inst = echarts.init(canvas);
            thumbInst.set(v.id, inst);
            try {
                inst.setOption(simplifyThumb(buildVariantOption(v, thumbCtx), v), true);
            } catch (err) {
                console.error('thumb render error:', v.id, err);
            }
        });
        syncSubListActive();
    }

    function syncSubListActive() {
        subList.querySelectorAll('.ct-thumb').forEach(c => {
            c.classList.toggle('active', c.dataset.type === selectedVariantId);
        });
    }

    // 缩略图简化：只保留图形，屏蔽所有文字/坐标轴/网格/标注，避免小图里看不清形状
    function simplifyThumb(opt) {
        if (!opt) return opt;
        opt.animation = false;
        delete opt.tooltip;
        if (opt.legend) opt.legend.show = false;
        if (opt.title) opt.title.show = false;
        if (opt.visualMap) opt.visualMap.show = false;
        if (opt.dataZoom) opt.dataZoom = [];
        if (opt.grid) { opt.grid.left = 1; opt.grid.right = 1; opt.grid.top = 1; opt.grid.bottom = 1; opt.grid.containLabel = false; }
        // 2D 坐标轴
        ['xAxis', 'yAxis', 'angleAxis', 'radiusAxis'].forEach(key => {
            if (!opt[key]) return;
            const axes = Array.isArray(opt[key]) ? opt[key] : [opt[key]];
            axes.forEach(ax => {
                ax.axisLabel = Object.assign(ax.axisLabel || {}, { show: false });
                ax.axisLine   = Object.assign(ax.axisLine   || {}, { show: false });
                ax.axisTick   = Object.assign(ax.axisTick   || {}, { show: false });
                ax.splitLine  = Object.assign(ax.splitLine  || {}, { show: false });
                ax.splitArea  = Object.assign(ax.splitArea  || {}, { show: false });
                ax.name = '';
            });
        });
        // 3D 坐标轴：echarts-gl 对隐藏 label 的 DOM 操作不健壮，改用透明/极小字号避免 null 报错
        ['xAxis3D', 'yAxis3D', 'zAxis3D'].forEach(key => {
            if (!opt[key]) return;
            opt[key].axisLabel = { color: 'transparent', fontSize: 0, interval: 10000 };
            opt[key].axisLine  = { lineStyle: { color: 'transparent' } };
            opt[key].axisTick  = { show: false };
            opt[key].name = '';
        });
        // 雷达图指标名
        if (opt.radar) {
            opt.radar.axisName = { show: false };
            opt.radar.splitLine = Object.assign(opt.radar.splitLine || {}, { show: false });
            opt.radar.axisLine  = Object.assign(opt.radar.axisLine  || {}, { show: false });
            opt.radar.splitArea = { show: false };
        }
        (opt.series || []).forEach(s => {
            if (s.label) s.label.show = false;
            if (s.markPoint) s.markPoint = undefined;
            if (s.markLine)  s.markLine  = undefined;
            if (s.markArea)  s.markArea  = undefined;
            if (s.emphasis)  s.emphasis  = { disabled: true };
        });
        return opt;
    }

    // 右侧主预览（大图）
    function renderMain() {
        if (!selectedVariantId) {
            const first = currentVariants()[0];
            if (!first) return;
            selectedVariantId = first.id;
            syncSubListActive();
        }
        const v = (VARIANTS[currentCat] || []).find(x => x.id === selectedVariantId);
        if (!v) return;
        const ctx = buildCtx();
        if (!mainInst) mainInst = echarts.init(previewCanvas);
        try {
            const opt = buildVariantOption(v, ctx);
            applyLabels(opt, v, ctx);
            mainInst.setOption(opt, true);
            mainInst.resize();
        } catch (err) {
            console.error('main render error:', v.id, err);
        }
        previewTitle.textContent = v.name;
        previewTools.dataset.variant = v.id;
    }

    // 数据标签注入（开关控制）：数值嵌入图形
    function applyLabels(opt, v, ctx) {
        if (!showLabels) return opt;
        const tc = ctx.textColor;
        (opt.series || []).forEach(s => {
            if (s.type === 'pie') {
                s.label = { show:true, color: tc, formatter: v.pctLabel ? '{b}: {d}%' : '{b}: {c}' };
            } else if (s.type === 'bar') {
                s.label = { show:true, position:'top', color: tc };
            } else if (s.type === 'line') {
                s.label = { show:true, position:'top', color: tc };
            } else if (s.type === 'scatter' || s.type === 'effectScatter') {
                s.label = { show:true, position:'top', color: tc, formatter: p => p.value[1] };
            } else if (s.type === 'funnel') {
                s.label = { show:true, position: v.funLabel ? 'inside' : 'right', color: tc };
            } else {
                s.label = { show:true, color: tc };
            }
        });
        return opt;
    }

    // 迷你数据表（主预览下方，当前 X/Y 列；开关控制显隐）
    function renderMiniTable() {
        if (!showTable || !chartData) { mainTable.style.display = 'none'; return; }
        const xKey = xAxisSelect.value;
        const yCols = selectedY();
        const rows = chartData.data || [];
        if (!rows.length || !yCols.length) { mainTable.style.display = 'none'; return; }
        const maxRows = Math.min(rows.length, 200);
        let html = '<table><thead><tr><th>' + xKey + '</th>' +
            yCols.map(c => '<th>' + c + '</th>').join('') + '</tr></thead><tbody>';
        for (let i = 0; i < maxRows; i++) {
            const row = rows[i];
            html += '<tr><td>' + (row[xKey] === undefined || row[xKey] === null ? '' : row[xKey]) + '</td>' +
                yCols.map(c => {
                    let v = row[c];
                    if (typeof v === 'string') v = parseFloat(v);
                    return '<td>' + (isNaN(v) ? 0 : v) + '</td>';
                }).join('') + '</tr>';
        }
        html += '</tbody></table>';
        mainTable.innerHTML = html;
        mainTable.style.display = 'block';
    }

    // ============ 变体 → ECharts option ============
    function buildVariantOption(v, ctx) {
        switch (v.kind) {
            case 'bar':       return buildBar(v, ctx);
            case 'line':      return buildLine(v, ctx);
            case 'pie':       return buildPie(v, ctx);
            case 'scatter':   return buildScatter(v, ctx);
            case 'radar':     return buildRadar(v, ctx);
            case 'funnel':    return buildFunnel(v, ctx);
            case 'treemap':   return buildTreemap(v, ctx);
            case 'pictorial': return buildPictorial(v, ctx);
            case 'histogram': return buildHistogram(v, ctx);
            case 'calendar':  return buildCalendar(v, ctx);
            case 'themeriver':return buildThemeRiver(v, ctx);
            case 'graph':     return buildGraph(v, ctx);
            case 'pareto':    return buildPareto(v, ctx);
            case 'stat':      return buildStat(v, ctx);
            default:          return buildBar(v, ctx);
        }
    }

    // ---------- 柱形族 ----------
    function buildBar(v, ctx) {
        const { xData, ySeries, xName, yNames, textColor: tc, axisColor: ac, palette } = ctx;
        const grid = gridDef();
        const legend = legendDef(yNames, tc);

        if (v.arrange === 'polar') {
            const angleAxis = catAxis(xData, xName, tc, ac);
            const radiusAxis = { type:'value', axisLabel:{ color: tc }, axisLine:{ lineStyle:{ color: ac } }, splitLine:{ lineStyle:{ color: ac, opacity:.35 } } };
            const series = ySeries.map((s, si) => { const o = decorateBar(s, v, si, palette, tc, ac, xData, ySeries); o.coordinateSystem = 'polar'; return o; });
            return {
                color: palette,
                tooltip:{ trigger:'item' },
                legend: legendDef(yNames, tc), polar:{}, angleAxis, radiusAxis, series
            };
        }
        if (v.arrange === 'horizontal') {
            const series = ySeries.map((s, si) => decorateBar(s, v, si, palette, tc, ac, xData, ySeries));
            return {
                color: palette,
                tooltip:{ trigger:'axis' }, legend, grid,
                xAxis: valAxis(tc, ac),
                yAxis: catAxis(xData, xName, tc, ac),
                series
            };
        }
        const series = ySeries.map((s, si) => decorateBar(s, v, si, palette, tc, ac, xData, ySeries));
        return {
            color: palette,
            tooltip:{ trigger:'axis' }, legend, grid,
            xAxis: catAxis(xData, xName, tc, ac),
            yAxis: valAxis(tc, ac),
            series
        };
    }

    function decorateBar(s, v, si, palette, tc, ac, xData, ySeriesAll) {
        const baseColor = palette[si % palette.length];
        const itemStyle = {};
        // 形状
        if (v.shape === 'round') itemStyle.borderRadius = 6;
        else if (v.shape === 'topround') itemStyle.borderRadius = [6, 6, 0, 0];
        else if (v.shape === 'capsule') itemStyle.borderRadius = 14;
        else if (v.shape === 'cylinder') {
            itemStyle.borderRadius = 999;
            itemStyle.color = new echarts.graphic.LinearGradient(0, 0, 1, 0, [
                { offset: 0, color: rgba(baseColor, 0.55) },
                { offset: 0.45, color: lighten(baseColor, 0.28) },
                { offset: 1, color: rgba(baseColor, 0.55) }
            ]);
        }
        else if (v.shape === 'shadow') { itemStyle.shadowBlur = 10; itemStyle.shadowColor = 'rgba(0,0,0,.28)'; itemStyle.shadowOffsetY = 4; }
        else if (v.shape === 'border') { itemStyle.borderColor = ac; itemStyle.borderWidth = 2; }
        else if (v.shape === 'hollow') { itemStyle.color = 'rgba(47,109,246,0.05)'; itemStyle.borderColor = baseColor; itemStyle.borderWidth = 2; }
        // 配色
        if (v.palette === 'vgrad') itemStyle.color = vGrad(baseColor);
        else if (v.palette === 'rgrad') itemStyle.color = rGrad(baseColor);
        else if (v.palette === 'stripe') itemStyle.color = stripeGrad(baseColor);
        else if (v.palette === 'alpha') itemStyle.opacity = 0.6;
        else if (v.palette === 'bgcolor') itemStyle.color = baseColor;
        else if (v.palette === 'highlight') { itemStyle.color = baseColor; itemStyle.borderRadius = 4; }
        else if (v.palette === 'threshold') {
            const all = s.data;
            const avg = all.length ? all.reduce((a, b) => a + b, 0) / all.length : 0;
            itemStyle.color = p => (p.value >= avg ? '#16a34a' : '#ef4444');
        }
        // 自定义形状（锥形/水滴/三角/山峰/立体/箭头/棒棒糖）
        if (CUSTOM_SHAPES.includes(v.shape)) {
            return {
                name: s.name, type:'custom',
                itemStyle:{ color: baseColor },
                data: xData.map((l, i) => [l, s.data[i]]),
                encode: { x: 0, y: 1 },
                renderItem: customBarRenderItem(v.shape, ySeriesAll.length)
            };
        }
        const out = { name: s.name, type:'bar', data: s.data, itemStyle };
        if (v.shape === 'markpoint') out.markPoint = { data:[{ type:'max', name:'最大' },{ type:'min', name:'最小' }] };
        if (v.shape === 'bg' || v.palette === 'bgcolor') { out.showBackground = true; out.backgroundStyle = { color:'rgba(150,150,150,.12)' }; }
        else if (v.palette === 'bgcolor') { out.showBackground = true; out.backgroundStyle = { color:'rgba(150,150,150,.18)' }; }
        if (v.arrange === 'stack') out.stack = 'total';
        if (v.arrange === 'pct') {
            out.stack = 'total';
            out.data = s.data.map((val, i) => {
                const tot = ySeriesAll.reduce((a, ss) => a + (ss.data[i] || 0), 0) || 1;
                return +(val / tot * 100).toFixed(2);
            });
            out.tooltip = { valueFormatter: x => x + '%' };
        }
        if (v.arrange === 'posneg') out.markLine = { silent:true, symbol:'none', lineStyle:{ color: ac }, data:[{ yAxis: 0 }] };
        if (v.arrange === 'target') {
            const avg = (s.data.reduce((a, b) => a + (b || 0), 0) / (s.data.length || 1)) || 0;
            out.markLine = { silent:true, symbol:'none', lineStyle:{ color: '#ef4444', type:'dashed', width: 2 }, data:[{ yAxis: +avg.toFixed(2) }], label:{ formatter:'目标 ' + avg.toFixed(1), color: tc } };
        }
        if (v.palette === 'highlight') out.emphasis = { focus:'series', itemStyle:{ shadowBlur: 12, shadowColor: baseColor } };
        if (v.palette === 'perclass') out.colorBy = 'data';
        return out;
    }

    // ---------- 折线族 ----------
    function buildLine(v, ctx) {
        const { xData, ySeries, xName, yNames, textColor: tc, axisColor: ac, palette } = ctx;
        const grid = gridDef();
        const legend = legendDef(yNames, tc);
        const catX = catAxis(xData, xName, tc, ac);
        const valY = valAxis(tc, ac);

        if (v.polar) {
            const angleAxis = catAxis(xData, xName, tc, ac);
            const radiusAxis = { type:'value', axisLabel:{ color: tc }, axisLine:{ lineStyle:{ color: ac } }, splitLine:{ lineStyle:{ color: ac, opacity:.35 } } };
            const series = ySeries.map((s, si) => { const o = decorateLine(s, v, si, palette, tc, ySeries, xData); o.coordinateSystem = 'polar'; return o; });
            return { tooltip:{ trigger:'item' }, legend: legendDef(yNames, tc), polar:{}, angleAxis, radiusAxis, series };
        }

        const series = ySeries.map((s, si) => decorateLine(s, v, si, palette, tc, ySeries, xData));
        const opt = {
            tooltip:{ trigger:'axis' }, legend, grid,
            xAxis: catX, yAxis: valY, series
        };
        if (v.zoom) opt.dataZoom = [{ type:'inside' }, { type:'slider', height: 16, bottom: 8 }];
        return opt;
    }

    function decorateLine(s, v, si, palette, tc, ySeriesAll, xData) {
        const baseColor = palette[si % palette.length];
        const out = { name: s.name, type:'line', data: s.data };
        if (v.smooth) out.smooth = true;
        if (v.step) out.step = v.step;
        if (v.dash) out.lineStyle = { type: v.dash };
        if (v.thick) out.lineStyle = Object.assign(out.lineStyle || {}, { width: 4 });
        if (v.lineGrad) out.lineStyle = Object.assign(out.lineStyle || {}, { color: vGrad(baseColor), width: 3 });
        if (v.symbol) { out.symbol = v.symbol; out.symbolSize = 9; out.showAllSymbol = true; }
        if (v.area) {
            out.areaStyle = v.areaGrad ? { color: vGrad(baseColor) } : {};
        }
        if (v.stack) out.stack = 'total';
        if (v.pct) {
            out.stack = 'total';
            out.data = s.data.map((val, i) => {
                const tot = ySeriesAll.reduce((a, ss) => a + (ss.data[i] || 0), 0) || 1;
                return +(val / tot * 100).toFixed(2);
            });
            out.tooltip = { valueFormatter: x => x + '%' };
        }
        if (v.bi) out.areaStyle = { opacity: 0.25 };
        if (v.mark === 'max') out.markPoint = { data:[{ type:'max' },{ type:'min' }] };
        if (v.mark === 'avg') out.markLine = { silent:true, data:[{ type:'average' }], lineStyle:{ color: tc } };
        if (v.mark === 'area') out.markArea = { silent:true, itemStyle:{ color:'rgba(47,109,246,.10)' }, data:[[{ xAxis: xData[0] || 0 },{ xAxis: xData[Math.floor(xData.length / 2)] || 0 }]] };
        out.itemStyle = { color: baseColor };
        out.lineStyle = Object.assign(out.lineStyle || {}, { color: baseColor });
        return out;
    }

    // ---------- 饼环族 ----------
    function buildPie(v, ctx) {
        const { xData, ySeries, textColor: tc, axisColor: ac, palette } = ctx;
        const agg = aggByX(xData, ySeries);
        const data = agg.map((a, i) => ({ name: a.label, value: a.total, itemStyle: v.pieGrad ? { color: vGrad(palette[i % palette.length]) } : undefined }));
        const legendData = xData;
        const legend = { data: legendData, textStyle:{ color: tc }, top: 10, type: v.legendScroll ? 'scroll' : 'plain' };
        const seriesBase = {
            type:'pie',
            radius: v.doughnut ? ['40%', '70%'] : '58%',
            center: ['50%', '56%'],
            data,
            label: { color: tc }
        };
        if (v.rose === 'radius') seriesBase.roseType = 'radius';
        if (v.rose === 'area') seriesBase.roseType = 'area';
        if (v.half) { seriesBase.startAngle = 180; seriesBase.endAngle = 0; seriesBase.radius = ['40%', '72%']; seriesBase.center = ['50%', '70%']; }
        if (v.pieBorder) seriesBase.itemStyle = { borderColor: '#fff', borderWidth: 2 };
        if (v.pieRound) seriesBase.itemStyle = Object.assign(seriesBase.itemStyle || {}, { borderRadius: 6 });
        if (v.pieShadow) seriesBase.itemStyle = Object.assign(seriesBase.itemStyle || {}, { shadowBlur: 10, shadowColor: 'rgba(0,0,0,.25)' });
        if (v.pieExplode) seriesBase.selectedMode = 'single', seriesBase.selectedOffset = 14;
        if (v.pctLabel) seriesBase.label = { color: tc, formatter: '{b}: {d}%' };
        if (v.centerTitle) {
            seriesBase.label = { show: false };
            seriesBase.center = ['50%', '50%'];
        }
        const series = [seriesBase];
        if (v.nested) {
            series.push({
                type:'pie', radius: ['18%', '34%'], center:['50%','56%'],
                data: agg.map((a, i) => ({ name: a.label, value: a.total })),
                label:{ show:false }, itemStyle:{ opacity:.85 }
            });
        }
        const opt = {
            color: palette,
            tooltip:{ trigger:'item', formatter: v.pctLabel ? '{b}: {d}%' : '{b}: {c}' },
            legend, series
        };
        if (v.centerTitle) {
            opt.title = { text: '总计', subtext: agg.reduce((s, a) => s + a.total, 0).toFixed(0), left:'center', top:'44%', textAlign:'center', textStyle:{ color: tc, fontSize: 14 }, subtextStyle:{ color: tc, fontSize: 22, fontWeight:'bold' } };
        }
        return opt;
    }

    // ---------- 散点族 ----------
    function buildScatter(v, ctx) {
        const { xData, ySeries, yNames, textColor: tc, axisColor: ac, palette } = ctx;
        const grid = gridDef();
        const xs = ySeries.length > 1 ? ySeries[1].data : xData.map((_, i) => i);
        const ys = ySeries[0].data;
        const pts = xs.map((x, i) => [x, ys[i]]);
        const baseColor = palette[0];
        const series = [];
        if (v.effect) {
            series.push({ type:'effectScatter', data: pts, symbolSize: 12, itemStyle:{ color: baseColor }, rippleEffect:{ scale: 3 } });
        } else if (v.scMultiSymbol) {
            const syms = ['circle','rect','triangle','diamond','pin'];
            for (let k = 0; k < syms.length; k++) {
                const sub = pts.filter((_, i) => i % syms.length === k);
                if (sub.length) series.push({ type:'scatter', data: sub, symbol: syms[k], symbolSize: 13, itemStyle:{ color: palette[k % palette.length] } });
            }
        } else {
            const out = { type:'scatter', data: pts, symbolSize: v.bubble ? (val => 8 + Math.abs(val[1]) % 26) : 12, itemStyle:{ color: v.scGrad ? vGrad(baseColor) : baseColor } };
            if (v.scSymbol) out.symbol = v.scSymbol;
            if (v.large) { out.large = true; out.largeThreshold = 500; }
            series.push(out);
        }
        if (v.trend) {
            const n = pts.length;
            let sx = 0, sy = 0, sxy = 0, sxx = 0;
            pts.forEach(([x, y]) => { sx += x; sy += y; sxy += x * y; sxx += x * x; });
            const slope = (n * sxy - sx * sy) / (n * sxx - sx * sx || 1);
            const intercept = (sy - slope * sx) / n;
            const xmin = Math.min(...xs), xmax = Math.max(...xs);
            series.push({ type:'line', data:[[xmin, slope * xmin + intercept],[xmax, slope * xmax + intercept]], showSymbol:false, lineStyle:{ type:'dashed', color: tc }, tooltip:{ show:false } });
        }
        return {
            tooltip:{ trigger:'item' }, grid,
            xAxis: { type:'value', name: ySeries.length > 1 ? yNames[1] : '序号', axisLabel:{ color: tc }, axisLine:{ lineStyle:{ color: ac } }, nameTextStyle:{ color: tc } },
            yAxis: { type:'value', name: yNames[0], axisLabel:{ color: tc }, axisLine:{ lineStyle:{ color: ac } }, nameTextStyle:{ color: tc } },
            series
        };
    }

    // ---------- 雷达族 ----------
    function buildRadar(v, ctx) {
        const { ySeries, yNames, textColor: tc, axisColor: ac, palette } = ctx;
        let indicators = yNames;
        if (v.radarMulti) indicators = yNames.concat(yNames.map(n => n + '₂'));
        const maxV = (ySeries.flatMap(s => s.data).reduce((a, b) => Math.max(a, b), 0) || 1) * 1.1;
        const series = [{
            type:'radar',
            data: ySeries.map((s, si) => ({
                name: s.name, value: s.data,
                lineStyle:{ color: palette[si % palette.length] },
                itemStyle:{ color: palette[si % palette.length] },
                areaStyle: v.radarArea ? { color: rgba(palette[si % palette.length], 0.18) } : undefined
            }))
        }];
        return {
            tooltip:{ trigger:'item' },
            legend:{ data: yNames, textStyle:{ color: tc }, top: 10 },
            radar: {
                indicator: indicators.map(n => ({ name: n, max: maxV })),
                axisName:{ color: tc }, splitLine:{ lineStyle:{ color: ac } },
                splitArea:{ show: false }, axisLine:{ lineStyle:{ color: ac } }
            },
            series
        };
    }

    // ---------- 漏斗族 ----------
    function buildFunnel(v, ctx) {
        const { xData, ySeries, textColor: tc, axisColor: ac, palette } = ctx;
        const agg = aggByX(xData, ySeries);
        const data = agg.map((a, i) => ({ name: a.label, value: a.total, itemStyle: v.funGrad ? { color: vGrad(palette[i % palette.length]) } : { color: palette[i % palette.length] } }));
        const series = [{ type:'funnel', data, label:{ color: tc, position: v.funLabel ? 'inside' : 'right' }, top: 20, bottom: 20, left:'8%', right:'8%' }];
        return { tooltip:{ trigger:'item' }, legend:{ data: xData, textStyle:{ color: tc }, top: 10, type:'scroll' }, series };
    }

    // ---------- 树图族 ----------
    function buildTreemap(v, ctx) {
        const { xData, ySeries, textColor: tc, palette } = ctx;
        let data;
        if (v.treeMulti) {
            data = xData.map((label, i) => ({
                name: label,
                children: ySeries.map((s, si) => ({ name: s.name, value: s.data[i] || 0, itemStyle:{ color: palette[si % palette.length] } }))
            }));
        } else {
            const agg = aggByX(xData, ySeries);
            data = agg.map((a, i) => ({ name: a.label, value: a.total, itemStyle:{ color: palette[i % palette.length] } }));
        }
        const series = [{ type:'treemap', data, label:{ color: tc }, top: 10, bottom: 10, left: 6, right: 6 }];
        if (v.treeBorder) series[0].itemStyle = { borderColor: tc, borderWidth: 1, gapWidth: 2 };
        return { tooltip:{ trigger:'item' }, series };
    }

    // ============ 统计科研类（2D 科研图 + echarts-gl 3D） ============
    function buildStat(v, ctx) {
        switch (v.stat) {
            case 'waterfall': return buildWaterfall(v, ctx);
            case 'stem':      return buildStem(v, ctx);
            case 'errorbar':  return buildErrorbar(v, ctx);
            case 'boxplot':   return buildBoxplot(v, ctx);
            case 'heatmap':   return buildHeatmap(v, ctx);
            case 'parallel':  return buildParallel(v, ctx);
            case 'gauge':     return buildGauge(v, ctx);
            case 'sankey':    return buildSankey(v, ctx);
            case 'sunburst':  return buildSunburst(v, ctx);
            case 'bar3d':     return buildBar3D(v, ctx);
            case 'surface':   return buildSurface(v, ctx);
            default:          return buildBar(v, ctx);
        }
    }

    function buildWaterfall(v, ctx) {
        const { xData, ySeries, xName, yNames, textColor: tc, axisColor: ac, palette } = ctx;
        const y = ySeries[0] || { name: yNames[0] || '值', data: [] };
        const base = [], inc = []; let cum = 0;
        y.data.forEach(val => { base.push(+cum.toFixed(2)); inc.push(+(val || 0).toFixed(2)); cum += +(val || 0); });
        return {
            tooltip:{ trigger:'axis', axisPointer:{ type:'shadow' } },
            legend:{ data:[y.name], textStyle:{ color: tc }, top:10 },
            grid: gridDef(),
            xAxis: catAxis(xData, xName, tc, ac),
            yAxis: valAxis(tc, ac),
            series: [
                { name:'placeholder', type:'bar', stack:'wf', itemStyle:{ color:'transparent' }, emphasis:{ itemStyle:{ color:'transparent' } }, data: base },
                { name: y.name, type:'bar', stack:'wf', itemStyle:{ color: palette[0] }, data: inc, label:{ show:true, position:'top', color: tc } }
            ]
        };
    }

    function buildStem(v, ctx) {
        const { xData, ySeries, xName, yNames, textColor: tc, axisColor: ac, palette } = ctx;
        const y = ySeries[0] || { name: yNames[0] || '值', data: [] };
        const color = palette[0];
        const d = y.data.map(x=>+(x||0).toFixed(2));
        return {
            tooltip:{ trigger:'axis' },
            legend:{ data:[y.name], textStyle:{ color: tc }, top:10 },
            grid: gridDef(),
            xAxis: catAxis(xData, xName, tc, ac),
            yAxis: valAxis(tc, ac),
            series: [
                { name: y.name, type:'bar', barWidth: 3, data: d, itemStyle:{ color } },
                { name: y.name, type:'scatter', data: d, symbolSize: 12, itemStyle:{ color }, z: 5 }
            ]
        };
    }

    function buildErrorbar(v, ctx) {
        const { xData, ySeries, xName, yNames, textColor: tc, axisColor: ac, palette } = ctx;
        const y = ySeries[0] || { name: yNames[0] || '值', data: [] };
        const color = palette[0];
        const data = xData.map((l, i) => {
            const val = +(y.data[i] || 0);
            const err = Math.max(Math.abs(val) * 0.12, 1);
            return [l, +val.toFixed(2), +err.toFixed(2)];
        });
        return {
            tooltip:{ trigger:'item' },
            legend:{ data:[y.name], textStyle:{ color: tc }, top:10 },
            grid: gridDef(),
            xAxis: catAxis(xData, xName, tc, ac),
            yAxis: valAxis(tc, ac),
            series: [
                { name: y.name, type:'custom', data, encode:{ x:0, y:1 }, renderItem: errorBarRenderItem },
                { name: y.name, type:'scatter', data: data.map(d=>[d[0], d[1]]), symbolSize: 7, itemStyle:{ color }, z: 5 }
            ]
        };
    }

    function buildBoxplot(v, ctx) {
        const { ySeries, yNames, textColor: tc, axisColor: ac, palette } = ctx;
        function quartiles(arr) {
            const a = arr.slice().sort((x,y)=>x-y);
            const q = p => { const idx = (a.length-1)*p, lo=Math.floor(idx), hi=Math.ceil(idx); return a[lo] + (a[hi]-a[lo])*(idx-lo); };
            return [a[0], +q(0.25).toFixed(2), +q(0.5).toFixed(2), +q(0.75).toFixed(2), a[a.length-1]];
        }
        const data = ySeries.map(s => quartiles(s.data.map(x=>(x||0))));
        return {
            tooltip:{ trigger:'item' },
            legend:{ data: yNames, textStyle:{ color: tc }, top:10 },
            grid: gridDef(),
            xAxis: { type:'category', data: yNames, axisLabel:{ color: tc }, axisLine:{ lineStyle:{ color: ac } } },
            yAxis: valAxis(tc, ac),
            series: [{ type:'boxplot', data, itemStyle:{ borderColor: tc, color: palette[0] } }]
        };
    }

    function buildHeatmap(v, ctx) {
        const { xData, ySeries, xName, yNames, textColor: tc, axisColor: ac } = ctx;
        const maxRows = Math.min(xData.length, 12);
        const xLabels = xData.slice(0, maxRows);
        const data = []; let min = Infinity, max = -Infinity;
        for (let i = 0; i < maxRows; i++) {
            for (let j = 0; j < ySeries.length; j++) {
                const val = +(ySeries[j].data[i] || 0);
                min = Math.min(min, val); max = Math.max(max, val);
                data.push([i, j, val]);
            }
        }
        return {
            tooltip:{ position:'top' },
            grid: { left: 90, right: 20, top: 30, bottom: 64, containLabel: true },
            xAxis: { type:'category', data: xLabels, axisLabel:{ color: tc, rotate: 30 }, axisLine:{ lineStyle:{ color: ac } } },
            yAxis: { type:'category', data: yNames, axisLabel:{ color: tc }, axisLine:{ lineStyle:{ color: ac } } },
            visualMap: { min: isFinite(min)?min:0, max: isFinite(max)?max:1, calculable: true, orient:'horizontal', left:'center', bottom: 4, textStyle:{ color: tc }, inRange:{ color: ['#2f6df6','#16a34a','#f59e0b','#ef4444'] } },
            series: [{ type:'heatmap', data, label:{ show:false } }]
        };
    }

    function buildParallel(v, ctx) {
        const { ySeries, yNames, textColor: tc, axisColor: ac, palette } = ctx;
        const parallelAxis = yNames.map((n, i) => ({ dim: i, name: n, axisLabel:{ color: tc }, axisLine:{ lineStyle:{ color: ac } }, nameTextStyle:{ color: tc } }));
        const len = Math.min(...ySeries.map(s=>s.data.length));
        const pdata = [];
        for (let i = 0; i < len; i++) pdata.push(ySeries.map(s => +(s.data[i] || 0).toFixed(2)));
        return {
            tooltip:{}, legend:{ data: yNames, textStyle:{ color: tc }, top:10 },
            parallelAxis, parallel: { left: 50, right: 50, top: 50, bottom: 30 },
            series: [{ type:'parallel', lineStyle:{ width: 2, color: palette[0] }, data: pdata }]
        };
    }

    function buildGauge(v, ctx) {
        const { ySeries, yNames, textColor: tc } = ctx;
        const vals = (ySeries[0] ? ySeries[0].data : []).filter(x=>isFinite(x));
        const maxV = (vals.length ? Math.max(...vals) : 1) * 1.1 || 1;
        const val = vals.length ? vals[0] : 0;
        return {
            tooltip:{ trigger:'item' },
            series: [{ type:'gauge', min:0, max: Math.ceil(maxV), startAngle: 210, endAngle: -30,
                progress:{ show:true, width: 14 }, axisLine:{ lineStyle:{ width: 14 } },
                axisLabel:{ color: tc }, title:{ color: tc }, detail:{ color: tc, formatter:'{value}' },
                data:[{ value: +val.toFixed(1), name: yNames[0] || '值' }] }]
        };
    }

    function buildSankey(v, ctx) {
        const { xData, ySeries, yNames, textColor: tc } = ctx;
        const maxRows = Math.min(xData.length, 10);
        const nodes = [];
        xData.slice(0, maxRows).forEach(l => nodes.push({ name: String(l) }));
        yNames.forEach(n => nodes.push({ name: n }));
        const links = [];
        for (let i = 0; i < maxRows; i++) {
            for (let j = 0; j < ySeries.length; j++) {
                const v2 = +(ySeries[j].data[i] || 0);
                if (v2 > 0) links.push({ source: String(xData[i]), target: yNames[j], value: v2 });
            }
        }
        return {
            tooltip:{ trigger:'item', triggerOn:'mousemove' },
            series:[{ type:'sankey', data: nodes, links, emphasis:{ focus:'adjacency' },
                label:{ color: tc }, lineStyle:{ color:'gradient', opacity:0.5 },
                nodeAlign:'left', left: 10, right: 110, top: 20, bottom: 20 }]
        };
    }

    function buildSunburst(v, ctx) {
        const { xData, ySeries, yNames, textColor: tc, palette } = ctx;
        const maxRows = Math.min(xData.length, 12);
        const data = xData.slice(0, maxRows).map((label, i) => ({
            name: String(label),
            children: ySeries.map((s, si) => ({ name: s.name, value: s.data[i] || 0, itemStyle:{ color: palette[si % palette.length] } }))
        }));
        return {
            tooltip:{ trigger:'item' },
            series:[{ type:'sunburst', data, radius:[0,'90%'],
                label:{ color: tc },
                levels:[{}, { r0:'0%', r:'42%' }, { r0:'42%', r:'100%', label:{ rotate:'tangential', color: tc } }] }]
        };
    }

    function buildBar3D(v, ctx) {
        const { xData, ySeries, yNames, textColor: tc, palette } = ctx;
        const maxRows = Math.min(xData.length, 12);
        const xs = xData.slice(0, maxRows);
        const data = []; let max = 0;
        for (let i = 0; i < maxRows; i++) {
            for (let j = 0; j < ySeries.length; j++) {
                const val = +(ySeries[j].data[i] || 0);
                max = Math.max(max, val);
                data.push([i, j, val]);
            }
        }
        return {
            tooltip:{},
            visualMap:{ max: max || 1, inRange:{ color: ['#2f6df6','#16a34a','#f59e0b','#ef4444'] }, textStyle:{ color: tc }, top: 10 },
            xAxis3D:{ type:'category', data: xs, axisLabel:{ color: tc } },
            yAxis3D:{ type:'category', data: yNames, axisLabel:{ color: tc } },
            zAxis3D:{ type:'value' },
            grid3D:{ axisLabel:{ color: tc }, axisLine:{ lineStyle:{ color: tc } }, splitLine:{ lineStyle:{ color: tc } }, viewControl:{ distance: 180 } },
            series:[{ type:'bar3D', data, shading:'color', label:{ show:false } }]
        };
    }

    function buildSurface(v, ctx) {
        const { xData, ySeries, yNames, textColor: tc } = ctx;
        const maxRows = Math.min(xData.length, 12);
        const xs = xData.slice(0, maxRows);
        const data = [];
        for (let i = 0; i < maxRows; i++) {
            for (let j = 0; j < ySeries.length; j++) {
                data.push([i, j, +(ySeries[j].data[i] || 0)]);
            }
        }
        const all = ySeries.length ? Math.max(1, ...ySeries.flatMap(s=>s.data.map(x=>Math.abs(x||0)))) : 1;
        return {
            tooltip:{},
            visualMap:{ max: all, inRange:{ color: ['#2f6df6','#16a34a','#f59e0b','#ef4444'] }, textStyle:{ color: tc }, top: 10 },
            xAxis3D:{ type:'category', data: xs, axisLabel:{ color: tc } },
            yAxis3D:{ type:'category', data: yNames, axisLabel:{ color: tc } },
            zAxis3D:{ type:'value' },
            grid3D:{ axisLabel:{ color: tc }, viewControl:{ distance: 180 } },
            series:[{ type:'surface', data, wireframe:{ show:true } }]
        };
    }

    // ============ 象形柱状图（pictorialBar） ============
    function buildPictorial(v, ctx) {
        const { xData, ySeries, xName, yNames, textColor: tc, axisColor: ac, palette } = ctx;
        const y = ySeries[0] || { name: yNames[0] || '值', data: [] };
        return {
            tooltip:{ trigger:'axis', axisPointer:{ type:'shadow' } },
            legend:{ data:[y.name], textStyle:{ color: tc }, top:10 },
            grid: gridDef(),
            xAxis: catAxis(xData, xName, tc, ac),
            yAxis: valAxis(tc, ac),
            series:[{
                name: y.name, type:'pictorialBar', symbol:'roundRect',
                symbolSize:[null, '80%'], symbolRepeat: true, symbolMargin: 3,
                symbolClip: false,
                itemStyle:{ color: palette[0] },
                data: y.data, label:{ show:false }
            }]
        };
    }

    // ============ 直方图（自动分箱） ============
    function buildHistogram(v, ctx) {
        const { yNames, textColor: tc, axisColor: ac, palette } = ctx;
        const y = ctx.ySeries[0] || { name: yNames[0] || '值', data: [] };
        const vals = y.data.map(x => +(x || 0)).filter(isFinite);
        const min = vals.length ? Math.min(...vals) : 0;
        const max = vals.length ? Math.max(...vals) : 1;
        const n = 10;
        const step = (max - min) / n || 1;
        const edges = []; for (let i = 0; i <= n; i++) edges.push(+(min + step * i).toFixed(1));
        const counts = new Array(n).fill(0);
        vals.forEach(val => {
            let idx = Math.floor((val - min) / step);
            if (idx >= n) idx = n - 1; if (idx < 0) idx = 0;
            counts[idx]++;
        });
        const labels = edges.slice(0, n).map((e, i) => e + '~' + edges[i + 1]);
        const grid = gridDef();
        return {
            tooltip:{ trigger:'axis', axisPointer:{ type:'shadow' }, formatter: p => (p[0] ? p[0].name + '：' + p[0].value + ' 个' : '') },
            legend:{ data:[y.name], textStyle:{ color: tc }, top:10 },
            grid,
            xAxis:{ type:'category', data: labels, name: y.name + ' 区间', axisLabel:{ color: tc, rotate: 30 }, axisLine:{ lineStyle:{ color: ac } }, nameTextStyle:{ color: tc } },
            yAxis: valAxis(tc, ac, '频数'),
            series:[{ type:'bar', data: counts, itemStyle:{ color: palette[0], borderRadius:[4,4,0,0] }, label:{ show:true, position:'top', color: tc } }]
        };
    }

    // ============ 帕累托图（柱 + 累计折线） ============
    function buildPareto(v, ctx) {
        const { xData, ySeries, xName, yNames, textColor: tc, axisColor: ac, palette } = ctx;
        const y = ySeries[0] || { name: yNames[0] || '值', data: [] };
        const pairs = xData.map((l, i) => ({ label: String(l), value: +(y.data[i] || 0) }));
        pairs.sort((a, b) => b.value - a.value);
        const total = pairs.reduce((s, p) => s + p.value, 0) || 1;
        let cum = 0;
        const cumPct = pairs.map(p => { cum += p.value; return +((cum / total) * 100).toFixed(1); });
        return {
            tooltip:{ trigger:'axis' },
            legend:{ data:[y.name, '累计占比'], textStyle:{ color: tc }, top:10 },
            grid: gridDef(),
            xAxis: catAxis(pairs.map(p => p.label), xName, tc, ac),
            yAxis: [
                valAxis(tc, ac),
                { type:'value', max:100, axisLabel:{ color: tc, formatter:'{value}%' }, axisLine:{ lineStyle:{ color: ac } }, splitLine:{ show:false } }
            ],
            series:[
                { name: y.name, type:'bar', data: pairs.map(p => p.value), itemStyle:{ color: palette[0], borderRadius:[4,4,0,0] } },
                { name:'累计占比', type:'line', yAxisIndex:1, data: cumPct, itemStyle:{ color: '#ef4444' }, lineStyle:{ color:'#ef4444', width:2 }, symbol:'circle', symbolSize:7 }
            ]
        };
    }

    // ============ 日历热力图（内置演示数据，全年） ============
    function buildCalendar(v, ctx) {
        const { textColor: tc, palette } = ctx;
        const year = 2026;
        const start = new Date(year, 0, 1);
        const data = [];
        let seed = 7;
        for (let d = 0; d < 365; d++) {
            const dt = new Date(start.getTime() + d * 86400000);
            const iso = dt.toISOString().slice(0, 10);
            seed = (seed * 9301 + 49297) % 233280;
            const val = Math.round((seed / 233280) * 100);
            data.push([iso, val]);
        }
        return {
            tooltip:{},
            visualMap:{ min:0, max:100, calculable:true, orient:'horizontal', left:'center', bottom:4, textStyle:{ color: tc }, inRange:{ color:['#e0ecff','#2f6df6','#f59e0b','#ef4444'] } },
            calendar:{ range: String(year), top:40, left:'center', cellSize:['auto', 14], itemStyle:{ color:'rgba(150,150,150,.08)', borderColor:'#fff' }, yearLabel:{ color: tc }, monthLabel:{ color: tc }, dayLabel:{ color: tc } },
            series:[{ type:'heatmap', coordinateSystem:'calendar', data, label:{ show:false } }]
        };
    }

    // ============ 主题河流图（内置演示数据） ============
    function buildThemeRiver(v, ctx) {
        const { textColor: tc } = ctx;
        const themes = ['主题A', '主题B', '主题C', '主题D'];
        const months = ['1月','2月','3月','4月','5月','6月','7月','8月','9月','10月','11月','12月'];
        const data = [];
        let seed = 13;
        themes.forEach((t, ti) => {
            months.forEach((m, mi) => {
                seed = (seed * 9301 + 49297) % 233280;
                const val = Math.round(20 + (seed / 233280) * 80 + Math.sin((mi + ti) * 0.8) * 20);
                data.push([m, val, t]);
            });
        });
        return {
            tooltip:{ trigger:'item' },
            legend:{ data: themes, textStyle:{ color: tc }, top:10 },
            singleAxis:{ type:'category', top:30, bottom:30, data: months, axisLabel:{ color: tc }, axisLine:{ lineStyle:{ color: tc } } },
            series:[{ type:'themeRiver', data, label:{ show:false }, itemStyle:{ opacity:0.82 } }]
        };
    }

    // ============ 关系网络图（基于类别合成） ============
    function buildGraph(v, ctx) {
        const { xData, ySeries, yNames, textColor: tc, palette } = ctx;
        const labels = xData.slice(0, 10).map(l => String(l));
        const nodes = labels.map((l, i) => ({ name: l, symbolSize: 22 + (i % 4) * 6, itemStyle:{ color: palette[i % palette.length] }, label:{ color: tc } }));
        const links = [];
        for (let i = 0; i < labels.length - 1; i++) links.push({ source: labels[i], target: labels[i + 1], lineStyle:{ color: tc, opacity:.4 } });
        if (labels.length > 2) links.push({ source: labels[0], target: labels[labels.length - 1], lineStyle:{ color: tc, opacity:.4 } });
        const opt = {
            tooltip:{ trigger:'item' },
            series:[{ type:'graph', layout: v.force ? 'force' : 'circular', roam:true,
                data: nodes, links,
                lineStyle:{ color:'source' },
                emphasis:{ focus:'adjacency' },
                label:{ show:true },
                force: v.force ? { repulsion: 130, edgeLength: 90 } : undefined
            }]
        };
        return opt;
    }

    // ============ 主预览工具条 ============
    function handleTool(act, id) {
        const inst = (id === selectedVariantId) ? mainInst : thumbInst.get(id) || mainInst;
        if (!inst) return;
        const name = TYPE_NAMES[id] || id;
        if (act === 'png') {
            const url = inst.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#fff' });
            const a = document.createElement('a');
            a.href = url; a.download = name + '.png';
            document.body.appendChild(a); a.click(); a.remove();
        } else if (act === 'copy') {
            copyImage(inst, name);
        } else if (act === 'csv') {
            downloadCSV(id);
        } else if (act === 'full') {
            if (previewPane.requestFullscreen) previewPane.requestFullscreen();
            else if (previewPane.webkitRequestFullscreen) previewPane.webkitRequestFullscreen();
        }
    }

    // 主预览工具条事件（固定容器，绑定一次）
    previewTools.addEventListener('click', e => {
        const btn = e.target.closest('.ct-tool');
        if (!btn || !selectedVariantId) return;
        handleTool(btn.dataset.act, selectedVariantId);
    });
    // 标签 / 数据表 开关
    labelToggle.addEventListener('change', () => {
        showLabels = labelToggle.checked;
        if (selectedVariantId) renderMain();
    });
    tableToggle.addEventListener('change', () => {
        showTable = tableToggle.checked;
        renderMiniTable();
    });

    async function copyImage(inst, name) {
        try {
            const url = inst.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#fff' });
            const blob = await (await fetch(url)).blob();
            if (!navigator.clipboard || !window.ClipboardItem) {
                toast('当前浏览器不支持复制图片，请改用「下载」'); return;
            }
            await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
            toast('已复制图片到剪贴板');
        } catch (err) {
            toast('复制失败（需 https 或 localhost 环境）：' + err.message);
        }
    }

    function downloadCSV(id) {
        const xKey = xAxisSelect.value;
        const yCols = selectedY();
        const rows = chartData.data || [];
        const lines = [[xKey].concat(yCols).join(',')];
        rows.forEach(r => {
            const row = [r[xKey]].concat(yCols.map(yc => {
                let v = r[yc];
                if (typeof v === 'string') v = parseFloat(v);
                return (isNaN(v) ? 0 : v);
            }));
            lines.push(row.join(','));
        });
        const csv = '﻿' + lines.join('\n');
        const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = '图表数据_' + (TYPE_NAMES[id] || id) + '_' + xKey + '.csv';
        document.body.appendChild(a); a.click(); a.remove();
        URL.revokeObjectURL(url);
    }

    // ============ 数据预览 ============
    function renderPreview() {
        const cols = chartData.columns || [];
        const rows = chartData.data || [];
        if (!cols.length || !rows.length) { previewBox.style.display = 'none'; return; }
        const maxRows = Math.min(rows.length, 100);
        let html = '<table><thead><tr>' + cols.map(c => `<th>${c}</th>`).join('') + '</tr></thead><tbody>';
        for (let i = 0; i < maxRows; i++) {
            const row = rows[i];
            html += '<tr>' + cols.map(c => {
                let v = row[c];
                if (v === null || v === undefined) v = '';
                return `<td>${v}</td>`;
            }).join('') + '</tr>';
        }
        html += '</tbody></table>';
        previewBox.innerHTML = html;
        previewBox.style.display = 'block';
    }

    // ============ 大类切换 ============
    catBar.addEventListener('click', e => {
        const btn = e.target.closest('.ct-cat');
        if (!btn) return;
        catBar.querySelectorAll('.ct-cat').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentCat = btn.dataset.cat;
        activeTags.clear();
        if (chartData) renderLayout(); else { buildFilterBar(); showEmpty(); }
    });

    // ============ 底部面板折叠 ============
    configToggle.addEventListener('click', () => configPanel.classList.toggle('collapsed'));

    // ============ 自适应 & 主题切换重绘 ============
    function resizeAll() {
        thumbInst.forEach(i => i.resize());
        if (mainInst) mainInst.resize();
    }
    window.addEventListener('resize', resizeAll);
    if (window.ResizeObserver) {
        const ro = new ResizeObserver(resizeAll);
        ro.observe(previewPane);
    }
    document.addEventListener('fullscreenchange', () => { setTimeout(resizeAll, 60); });
    const bodyObserver = new MutationObserver(() => { if (chartData) renderLayout(); });
    bodyObserver.observe(document.body, { attributes: true, attributeFilter: ['class', 'data-light-theme'] });

    // 初始
    buildFilterBar();
    showEmpty();
});
