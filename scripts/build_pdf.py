#!/usr/bin/env python3
"""Build the submission PDF from Markdown and actual observation files."""
import csv
import datetime as dt
import html
import io
import json
import os
from pathlib import Path
import re
import zipfile

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle, KeepTogether
from reportlab.graphics.shapes import Drawing, Line, String, Rect, PolyLine
from summarize import read_case

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output/pdf'
OUT.mkdir(parents=True, exist_ok=True)
FONT = os.environ.get('KOREAN_FONT', '/Library/Fonts/Arial Unicode.ttf')
pdfmetrics.registerFont(TTFont('KR', FONT))
pdfmetrics.registerFontFamily('KR', normal='KR', bold='KR', italic='KR', boldItalic='KR')
NAVY = colors.HexColor('#18324D')
TEAL = colors.HexColor('#007F85')
RED = colors.HexColor('#BD4C3E')
BLUE = colors.HexColor('#3976BF')
GRAY = colors.HexColor('#576474')
LIGHT = colors.HexColor('#EEF3F7')
WIDTH = A4[0] - 88
styles = {
 'body': ParagraphStyle('body',fontName='KR',fontSize=9.3,leading=15,spaceAfter=7,wordWrap='CJK',textColor=NAVY),
 'h1': ParagraphStyle('h1',fontName='KR',fontSize=17,leading=24,spaceAfter=14,keepWithNext=True,textColor=NAVY,wordWrap=None),
 'h2': ParagraphStyle('h2',fontName='KR',fontSize=12.3,leading=18,spaceBefore=12,spaceAfter=7,keepWithNext=True,textColor=TEAL),
 'h3': ParagraphStyle('h3',fontName='KR',fontSize=10.8,leading=16,spaceBefore=8,spaceAfter=6,keepWithNext=True,textColor=TEAL),
 'code': ParagraphStyle('code',fontName='Courier',fontSize=6.8,leading=9.5,spaceAfter=0,wordWrap='CJK',textColor=colors.HexColor('#24394E')),
 'cell': ParagraphStyle('cell',fontName='KR',fontSize=8,leading=12,wordWrap='CJK',textColor=NAVY),
 'caption': ParagraphStyle('caption',fontName='KR',fontSize=8,leading=12,spaceAfter=10,wordWrap='CJK',textColor=GRAY),
}


def inline(text):
    text = html.escape(text)
    def link(m):
        label, target = m.groups()
        if target.startswith('https://'):
            return f'<link href="{target}" color="#007F85">{label}</link>'
        # Relative links refer to attached project evidence, not a remote site.
        return f'{label} <font color="#576474">({target})</font>'
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', link, text)
    text = re.sub(r'`([^`]+)`', r'<font color="#007F85">\1</font>', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
    return text


def P(text, style='body'):
    return Paragraph(inline(text), styles[style])


def chart(series, ymax, ylabel, title, width=WIDTH, height=190, xmin=0, xmax=None):
    d = Drawing(width, height)
    x0,y0,w,h = 45,33,width-60,height-66
    xmax = xmax or max(x for _, pts, _ in series for x,y in pts)
    d.add(String(0,height-11,title,fontName='KR',fontSize=10,fillColor=NAVY))
    d.add(String(0,height-29,ylabel,fontName='KR',fontSize=7,fillColor=GRAY))
    for i in range(5):
        yy=y0+i*h/4
        d.add(Line(x0,yy,x0+w,yy,strokeColor=colors.HexColor('#DCE4EC'),strokeWidth=.5))
        d.add(String(x0-5,yy-2,f'{ymax*i/4:g}',fontName='Helvetica',fontSize=7,textAnchor='end',fillColor=GRAY))
    for i in range(6):
        xx=x0+i*w/5
        d.add(String(xx,y0-13,f'{xmin+(xmax-xmin)*i/5:.0f}',fontName='Helvetica',fontSize=7,textAnchor='middle',fillColor=GRAY))
    d.add(String(x0+w,y0-26,'seconds since launch',fontName='Helvetica',fontSize=7,textAnchor='end',fillColor=GRAY))
    for idx,(label,pts,color) in enumerate(series):
        coords=[]
        for x,y in pts:
            if xmin <= x <= xmax:
                coords.extend([x0+(x-xmin)/(xmax-xmin)*w,y0+y/ymax*h])
        if len(coords)>=4:
            d.add(PolyLine(coords,strokeColor=color,strokeWidth=1.4))
        lx=x0+75+idx*((w-75)/max(len(series),1))
        d.add(Line(lx,height-26,lx+10,height-26,strokeColor=color,strokeWidth=2))
        d.add(String(lx+14,height-28,label,fontName='KR',fontSize=7,fillColor=GRAY))
    return d


def seconds(stamp,start):
    return (dt.datetime.fromisoformat(stamp)-dt.datetime.fromisoformat(start)).total_seconds()


def figures(kind):
    if kind=='oom':
        series=[]
        for name,label,color in [('oom/before','100MB',RED),('oom/after','200MB',BLUE),('cpu/after','512MB 추가 검증',TEAL)]:
            info,rows=read_case(name)
            series.append((label,[(seconds(r['timestamp'],info['started_at']),r['rss_kb']/1024) for r in rows],color))
        return [chart(series,600,'RSS (MiB)','실측 메모리 추이'),P('약 1초 간격 monitor.sh 원본. 100/200MB는 종료 직전까지 누적, 512MB는 캐시 정리 후 RSS 감소.','caption')]
    if kind=='cpu':
        info,rows=read_case('cpu/before-02')
        data=list(csv.DictReader((ROOT/'evidence/cpu/before-02/cpu-burst-1089.csv').open()))
        pts=[(seconds(r['timestamp'],info['started_at']),float(r['cpu_percent'])) for r in data]
        return [chart([('약 50ms 구간 CPU',pts,RED)],100,'CPU (% of one core)','Watchdog 종료 직전 실제 CPU burst',xmin=26,xmax=31),P('PID 1089의 마지막 5초. 최대 78.6%는 약 50ms 평균이다. 앱 내부 Load 52.69% 및 1초 관제 최대 5.0%와 구분한다.','caption')]
    if kind=='deadlock':
        d=Drawing(WIDTH,135)
        for x,title,held,wait in [(10,'Worker-1','Holding: A','Waiting: B'),(WIDTH-210,'Worker-2','Holding: B','Waiting: A')]:
            d.add(Rect(x,25,200,92,rx=7,ry=7,fillColor=LIGHT,strokeColor=colors.HexColor('#D7E1E9')))
            d.add(String(x+15,94,title,fontName='Helvetica-Bold',fontSize=12,fillColor=NAVY))
            d.add(String(x+15,70,held,fontName='Helvetica',fontSize=10,fillColor=TEAL))
            d.add(String(x+15,49,wait,fontName='Helvetica',fontSize=10,fillColor=RED))
        d.add(Line(210,88,WIDTH-213,88,strokeColor=RED,strokeWidth=1.4))
        d.add(String(WIDTH/2,96,'wait',fontName='Helvetica',fontSize=8,textAnchor='middle',fillColor=RED))
        d.add(Line(210,55,WIDTH-213,55,strokeColor=RED,strokeWidth=1.4))
        d.add(Line(WIDTH-213,88,WIDTH-220,92,strokeColor=RED))
        d.add(Line(WIDTH-213,88,WIDTH-220,84,strokeColor=RED))
        d.add(Line(210,55,217,59,strokeColor=RED))
        d.add(Line(210,55,217,51,strokeColor=RED))
        d.add(String(WIDTH/2,39,'cycle',fontName='Helvetica',fontSize=8,textAnchor='middle',fillColor=RED))
        return [d,P('A = Shared_Memory_A, B = Socket_Pool_B. 로그의 락 보유·요청 관계를 재구성한 도식이다.','caption')]
    return []


def markdown(path,kind):
    lines=path.read_text().splitlines()
    result=[]; i=0
    while i<len(lines):
        line=lines[i]
        if not line.strip():
            i+=1; continue
        if line.startswith('```'):
            block=[]; i+=1
            while i<len(lines) and not lines[i].startswith('```'):
                # Wrap long terminal lines at character boundaries to preserve text.
                text=lines[i].expandtabs(4)
                while len(text)>108:
                    block.append(text[:108]); text=text[108:]
                block.append(text); i+=1
            rows=[[Paragraph(html.escape(x).replace(' ','&nbsp;') or '&nbsp;', styles['code'])] for x in block]
            t=Table(rows,colWidths=[WIDTH],hAlign='LEFT')
            t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),LIGHT),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),2),('BOTTOMPADDING',(0,0),(-1,-1),2)]))
            result += [KeepTogether([t]),Spacer(1,9)]; i+=1; continue
        if line.startswith('|'):
            rows=[]
            while i<len(lines) and lines[i].startswith('|'):
                cells=[x.strip() for x in lines[i].strip().strip('|').split('|')]
                if not all(re.fullmatch(r'[:\- ]+',x) for x in cells):
                    rows.append([P(x,'cell') for x in cells])
                i+=1
            widths=[WIDTH/len(rows[0])]*len(rows[0])
            if len(widths)==3: widths=[WIDTH*.25,WIDTH*.375,WIDTH*.375]
            t=Table(rows,colWidths=widths,repeatRows=1,hAlign='LEFT')
            t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#DCE9ED')),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,LIGHT]),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),('LINEBELOW',(0,0),(-1,0),.6,TEAL)]))
            result += [t,Spacer(1,9)]; continue
        if line.startswith('# '):
            result.append(P(line[2:],'h1')); result.extend(figures(kind)); i+=1; continue
        if line.startswith('## '):
            if kind in ('oom','cpu','deadlock') and line.startswith('## 4.'):
                result.append(PageBreak())
            result.append(P(line[3:],'h2')); i+=1; continue
        if line.startswith('### '):
            result.append(P(line[4:],'h3')); i+=1; continue
        para=[line]
        i+=1
        while i<len(lines) and lines[i].strip() and not lines[i].startswith(('#','```','|')):
            para.append(lines[i]); i+=1
        paragraph=P(' '.join(para))
        next_line=i
        while next_line<len(lines) and not lines[next_line].strip():
            next_line+=1
        if next_line<len(lines) and lines[next_line].startswith('```'):
            paragraph.keepWithNext=True
        result.append(paragraph)
    return result


def footer(canvas,doc):
    canvas.setStrokeColor(colors.HexColor('#DCE4EC'))
    canvas.line(44,38,A4[0]-44,38)
    canvas.setFont('KR',7)
    canvas.setFillColor(GRAY)
    canvas.drawString(44,25,'B1-2 | 시스템 장애 분석 | 2026-09-05 KST')
    canvas.drawRightString(A4[0]-44,25,str(doc.page))


story=[Spacer(1,40),P('B1-2 · 운영체제 장애 분석','h2'),P('시스템 장애 분석 및 이슈 리포트','h1'),P('OOM Crash · CPU Latency · Deadlock'),Spacer(1,15),P('실제 실행 로그를 근거로 재현, 원인 추론, 임시 조치와 결과를 검증했다. 제공 바이너리는 수정하거나 디컴파일하지 않았다.'),P('실험일: 2026-09-05 / 모든 본문 시각은 KST'),P('환경: Ubuntu 22.04.5 LTS, Linux 6.17.8 x86_64, 일반 사용자 analyst (UID 1000). OrbStack Docker 컨테이너에 메모리 1GiB·CPU 상한 2코어를 적용했다.'),Spacer(1,12)]
cover=[['사례','주요 결과'],['OOM','100 → 200MB: 생존 11.412 → 23.687초. 512MB 추가 검증에서는 캐시 정리 확인.'],['CPU','100 → 40%: Watchdog 종료에서 75초 관측 중 생존 및 cooldown으로 전환.'],['Deadlock','true → false: 순환 자원 대기에서 작업 진행으로 전환.'],['보너스','등록된 A/B/C 순차 완료는 앱 수준의 FCFS 패턴과 부합.']]
t=Table([[P(c,'cell') for c in row] for row in cover],colWidths=[75,WIDTH-75])
t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#DCE9ED')),('ROWBACKGROUNDS',(0,1),(-1,-1),[LIGHT,colors.white]),('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),11),('BOTTOMPADDING',(0,0),(-1,-1),11)]))
story += [t,Spacer(1,18),P('증거 읽기','h2'),P('본문은 필수 증거와 Before/After 비교를 포함한다. 전체 원본 로그는 이 PDF에 evidence.zip으로 첨부했다. PDF 뷰어의 첨부파일 패널에서 추출하거나 함께 제공된 프로젝트의 evidence 폴더를 열면 된다.'),P('앱의 Heap/Current Load, OS의 RSS/CPU, 컨테이너 자원 상한은 서로 다른 지표이다. 자체 보호 종료와 실험 종료용 SIGTERM도 구분했다. 관측 기간의 회피 효과를 장기 무장애 보장으로 해석하지 않는다.'),PageBreak()]
for kind in ['oom','cpu','deadlock','scheduling']:
    story += markdown(ROOT/f'reports/{kind}.md',kind)
    story.append(PageBreak())
story += [P('증거 인덱스와 재현 안내','h1'),P('GitHub Issue 형식의 Markdown 원문은 reports/oom.md, reports/cpu.md, reports/deadlock.md이다. 보너스 원문은 reports/scheduling.md에 있다.'),P('정식 비교: oom/before, oom/after, cpu/before-02, cpu/after, deadlock/before, deadlock/after. 각 폴더의 app.log, monitor-PID.log, ps.txt, top.txt, settings.json, result.json, preflight.txt, postflight.txt를 보존했다.'),P('탐색 2회는 일반 리다이렉션으로 마지막 print가 유실될 수 있어, 정식 실험에서는 PTY로 표준 출력을 수집했다. CRLF만 LF로 정규화했고 앱 자체 로그도 별도 복사했다. 50ms 샘플은 CPU 정식 사례에 추가했다.'),P('생존 시간은 실행 시작부터 부모 회수와 관측기 정리까지의 monotonic 시간이며 작은 계측 오버헤드가 포함된다. JSON/CSV/ps 헤더는 UTC, 앱·monitor는 KST이다. RSS_KB는 KiB, 1코어 CPU=100% 기준이다.'),P('재현: Docker 환경에서 bash scripts/run_experiments.sh -rerun-01을 실행한다. 실행기는 기존 증거를 덮어쓰지 않는다. 폴더·키·권한·환경변수 준비와 비root 검증을 포함한다.'),P('바이너리 SHA-256','h2'),P('7e0a19cfa80ece6b547a5008273661f0d4d71e526e96b51e0d0f341dd1bb3e40','caption'),P('원본 무결성 및 제출 범위','h2'),P('첨부 evidence.zip 안의 SHA256SUMS로 원본 로그를 검증할 수 있다. 이 PDF는 제출용으로 완결된 보고서이며 GitHub에는 게시하지 않았다. 검증 기록과 실행·PDF 생성 스크립트는 프로젝트에 함께 제공한다.')]
buffer=io.BytesIO()
doc=SimpleDocTemplate(buffer,pagesize=A4,leftMargin=44,rightMargin=44,topMargin=43,bottomMargin=52,title='B1-2 시스템 장애 분석 및 이슈 리포트',author='B1-2 실험 보고서')
doc.build(story,onFirstPage=footer,onLaterPages=footer)
archive=io.BytesIO()
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
    for path in sorted((ROOT/'evidence').rglob('*')):
        if path.is_file(): z.write(path,path.relative_to(ROOT).as_posix())
    for name in ['scripts', 'reports', 'README.md', 'Dockerfile', 'monitor.sh', '.env.example', 'requirement.md']:
        path=ROOT/name
        items=sorted(path.rglob('*')) if path.is_dir() else [path]
        for item in items:
            if item.is_file() and '__pycache__' not in item.parts:
                z.write(item,item.relative_to(ROOT).as_posix())
writer=PdfWriter()
writer.append(PdfReader(buffer))
writer.add_attachment('evidence.zip',archive.getvalue())
writer.add_metadata({'/Title':'B1-2 시스템 장애 분석 및 이슈 리포트','/Author':'B1-2 실험 보고서'})
file=OUT/'system-incident-reports.pdf'
with file.open('wb') as f: writer.write(f)
print(f'{file}: {len(writer.pages)} pages, {file.stat().st_size:,} bytes')
