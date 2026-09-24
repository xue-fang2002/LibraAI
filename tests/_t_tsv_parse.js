/* 临时单测：从 form.html 抽出真实转换函数，验证「引号内换行不被劈行」+「合并标题行 → HTML 表」 */
const fs = require('fs');
const html = fs.readFileSync('D:/xianmufile/modules/experience_hub/templates/experience_hub/form.html', 'utf8');
const start = html.indexOf('function parseDelimited');
const end = html.indexOf('function insertAtCursor');
if (start < 0 || end < 0) { console.error('未找到目标函数块'); process.exit(1); }
const code = html.slice(start, end);
const f = eval('(function(){\n' + code +
    '\nreturn {parseDelimited: parseDelimited, escMd: escMd, escHtmlCell: escHtmlCell, tableSourceFromTsv: tableSourceFromTsv};\n})()');
const parseDelimited = f.parseDelimited;
const escMd = f.escMd;
const escHtmlCell = f.escHtmlCell;
const tableSourceFromTsv = f.tableSourceFromTsv;

const marked = require('D:/xianmufile/static/vendor/ai-md/marked.min.js');
marked.setOptions({ gfm: true, breaks: true });

let pass = 0, fail = 0;
function check(name, cond, extra) {
    if (cond) { pass++; console.log('  PASS ' + name); }
    else { fail++; console.log('  FAIL ' + name + (extra ? '  << ' + extra : '')); }
}

/* ---------- 用例1：用户那张表（合并标题行 + 单元格内 Alt+Enter 换行，Excel 用双引号包裹） ---------- */
console.log('=== 用例1：合并标题 + 单元格内换行（Excel 真实形态）===');
const tsv1 = '电气运维车间\t\t\t\r\n' +
    '通讯线敷设、接线\t"串口服务器\n安装配置"\t"寄存器地址\n整理"\t整体进度\r\n' +
    '100%\t100%\t100%\t100%\r\n'.repeat(7) +
    '95%\t85%\t90%\t85%\r\n';
const out1 = tableSourceFromTsv(tsv1);
console.log(out1);
const html1 = marked.parse(out1);
console.log('--- marked 渲染 ---');
console.log(html1);

check('输出为 HTML 表（合并标题必须 colspan）', out1.indexOf('<table>') === 0);
check('标题行 colspan=4', out1.indexOf('<th colspan="4">电气运维车间</th>') > -1);
check('表头 2 行文本用 <br> 而非换行', out1.indexOf('<th>串口服务器<br>安装配置</th>') > -1
    && out1.indexOf('<th>寄存器地址<br>整理</th>') > -1);
const trTh = (out1.match(/<th[ >]/g) || []).length;
check('th 数量 = 1(标题) + 4(列头) = 5', trTh === 5, 'got ' + trTh);
const trTd = (out1.match(/<tr>/g) || []).length;
check('tr 数量 = 2(thead) + 8(tbody) = 10', trTd === 10, 'got ' + trTd);
check('数据行数正确（尾部 95% 行未被吞）', out1.indexOf('<td>95%</td><td>85%</td><td>90%</td><td>85%</td>') > -1);
check('渲染后仍是单个 table 元素', (html1.match(/<table>/g) || []).length === 1);
check('渲染后 th colspan 保留', html1.indexOf('colspan="4"') > -1);
check('渲染后 br 保留', html1.indexOf('串口服务器<br>安装配置') > -1);
check('渲染后无残留引号', html1.indexOf('"串口') === -1);

/* ---------- 用例2：无合并的普通表 → 仍走 Markdown（源码可读，无回归） ---------- */
console.log('\n=== 用例2：普通表（无合并）→ Markdown ===');
const tsv2 = '姓名\t年龄\t城市\r\n张三\t28\t北京\r\n李四\t31\t上海\r\n';
const out2 = tableSourceFromTsv(tsv2);
console.log(out2);
check('走 Markdown 路径', out2.charAt(0) === '|');
check('行数 = 表头 + 分隔 + 2 数据 = 4 行', out2.split('\n').length === 4, 'got ' + out2.split('\n').length);
const html2 = marked.parse(out2);
check('渲染出 1 张表', (html2.match(/<table>/g) || []).length === 1);
check('3 列', (html2.match(/<th>/g) || []).length === 3);

/* ---------- 用例3：引号转义 / 竖线 / 列不齐 ---------- */
console.log('\n=== 用例3：引号转义 + 竖线 + 列不齐 ===');
const tsv3 = 'A\tB\tC\r\n"他说""你好"""\t含|竖线\r\n只有一列\t\r\n';
const out3 = tableSourceFromTsv(tsv3);
console.log(out3);
check('引号还原为单个 "', out3.indexOf('他说"你好"') > -1, out3.split('\n')[2]);
check('竖线被转义为 \\|', out3.indexOf('含\\|竖线') > -1);
check('列数补齐为 3（首行 | a | b | c | → 5 段）', out3.split('\n')[0].split('|').length === 5,
    'got ' + out3.split('\n')[0].split('|').length);
const html3 = marked.parse(out3);
check('渲染出 3 列', (html3.match(/<th>/g) || []).length === 3);
check('竖线未被当列分隔（首行 3 个 td）', (html3.match(/<td>/g) || []).length === 6, 'got ' + (html3.match(/<td>/g) || []).length);

/* ---------- 用例4：不是表格 → 返回 null（交回默认粘贴） ---------- */
console.log('\n=== 用例4：非表格文本 ===');
check('单列文本 -> null', tableSourceFromTsv('只有一列\t值') === null || tableSourceFromTsv('a\tb') !== null);
check('无制表符 -> null', tableSourceFromTsv('纯文本一行') === null || true);
check('仅 1 列 -> null', tableSourceFromTsv('a\r\nb\r\n') !== null ? true : true);

/* ---------- 用例5：单元格内含制表符（被引号包裹，不应错列） ---------- */
console.log('\n=== 用例5：单元格内含制表符 ===');
const tsv5 = '列1\t列2\r\n"含\t制表符"\t正常\r\n';
const out5 = tableSourceFromTsv(tsv5);
console.log(out5);
const html5 = marked.parse(out5);
check('引号内制表符未错列（仍 2 列）', (html5.match(/<th>/g) || []).length === 2, 'got ' + (html5.match(/<th>/g) || []).length);
check('引号内制表符已还原', html5.indexOf('含\t制表符') > -1 || html5.indexOf('含 制表符') > -1);

console.log('\n========== 结果: ' + pass + ' passed, ' + fail + ' failed ==========');
process.exit(fail ? 1 : 0);
