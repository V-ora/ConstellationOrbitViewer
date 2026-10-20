# -*- coding: utf-8 -*-
"""离线单元测试：证明 parse_wiki_launches 能按**表头列位**取到研制方，
并且某行中间有空单元格时不会串列（这是最容易错的地方）。

表格结构照抄线上词条的真实表头（2026-10-06 抓取快照）：
  千帆 组网星：序号 中文名 英文名 研发机构 发射日期 运载火箭 发射基地 轨道高度 轨道倾角 轨道平面 质量 COSPAR 备注
  星网 业务星：发射次数 名称 部署颗数 研制单位 发射时间 运载火箭 发射地点 轨道高度 轨道倾角 COSPAR 结果 备注
"""
import io, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import server as S

QF = """<h2>组网星</h2><table>
<tr><th>序号</th><th>中文名</th><th>英文名</th><th>研发机构</th><th>发射日期</th>
<th>运载火箭</th><th>发射基地</th><th>轨道高度</th><th>轨道倾角</th><th>轨道平面</th>
<th>质量</th><th>COSPAR</th><th>备注</th></tr>
<tr><td>1</td><td>千帆极轨01组卫星（共18颗）</td><td>QIANFAN-1~18</td>
<td>上海微小卫星工程中心</td><td>2024年08月06日 14:42</td><td>长征六号甲Y21</td>
<td>太原 LC-9A</td><td>800 km</td><td>89.0°</td><td>280°</td><td>300 kg</td>
<td>2024-140</td><td></td></tr>
<tr><td>2</td><td>千帆极轨02组卫星（共18颗）</td><td>QIANFAN-19~36</td>
<td>格思航天</td><td>2024年10月15日 19:06</td><td>长征六号甲Y20</td>
<td>太原 LC-9A</td><td>800 km</td><td>89.0°</td><td>60°</td><td></td>
<td>2024-185</td><td></td></tr>
</table>"""

# ★ 陷阱：这一行「质量」列是空的 —— 若先滤掉空单元格再按下标取，
#   研制方就会取到「长征六号甲Y22」。用原始列位就不会错。
GW = """<h2>业务星</h2><table>
<tr><th>发射次数</th><th>名称</th><th>部署颗数</th><th>研制单位</th><th>发射时间</th>
<th>运载火箭</th><th>发射地点</th><th>轨道高度</th><th>轨道倾角</th><th>COSPAR</th>
<th>结果</th><th>备注</th></tr>
<tr><td>4</td><td>卫星互联网低轨01组</td><td>10</td><td>五院</td>
<td>2024年12月16日 18:00</td><td>长征五号B Y6/远征二号Y2</td><td>文昌 LC-101</td>
<td>86.5°</td><td>86.5°</td><td>2024-240</td><td>成功</td><td></td></tr>
<tr><td>7</td><td>卫星互联网低轨04组</td><td>5</td><td>中国科学院微小卫星创新研究院</td>
<td>2025年06月06日 04:45</td><td></td><td>太原 LC-9A</td>
<td>86.5°</td><td>86.5°</td><td>2025-121</td><td>成功</td><td></td></tr>
</table>"""

ok = fail = 0


def chk(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("PASS", name, extra)
    else:
        fail += 1
        print("FAIL", name, extra)


r = S.parse_wiki_launches(QF)
rows = {x["cospar"]: x for x in r.get("qianfan", [])}
chk("千帆 01 组研制方", rows.get("2024-140", {}).get("dev") == "上海微小卫星工程中心",
    rows.get("2024-140", {}).get("dev"))
chk("千帆 02 组研制方（中间空单元格不串列）",
    rows.get("2024-185", {}).get("dev") == "格思航天", rows.get("2024-185", {}).get("dev"))
chk("千帆火箭没被研制方列串掉",
    rows.get("2024-185", {}).get("rocket") == "长征六号甲Y20",
    rows.get("2024-185", {}).get("rocket"))
chk("千帆发射时间仍正确",
    rows.get("2024-185", {}).get("time") == "2024-10-15 19:06",
    rows.get("2024-185", {}).get("time"))

g = S.parse_wiki_launches(GW)
grows = {x["cospar"]: x for x in g.get("gw", [])}
chk("星网 01 组研制方", grows.get("2024-240", {}).get("dev") == "五院",
    grows.get("2024-240", {}).get("dev"))
chk("星网 04 组研制方（火箭列为空也不串）",
    grows.get("2025-121", {}).get("dev") == "中国科学院微小卫星创新研究院",
    grows.get("2025-121", {}).get("dev"))
chk("星网 01 组运载火箭正确",
    grows.get("2024-240", {}).get("rocket") == "长征五号B Y6/远征二号Y2",
    grows.get("2024-240", {}).get("rocket"))
chk("星网 04 组发射场正确", grows.get("2025-121", {}).get("site") == "太原 LC-9A",
    grows.get("2025-121", {}).get("site"))

# 表头行本身不能被当成数据行
chk("表头行未被误当数据", len(r.get("qianfan", [])) == 2, len(r.get("qianfan", [])))
chk("星网表头行未被误当数据", len(g.get("gw", [])) == 2, len(g.get("gw", [])))

print()
print("== 单元测试: %d 通过 / %d 失败 ==" % (ok, fail))
sys.exit(1 if fail else 0)
