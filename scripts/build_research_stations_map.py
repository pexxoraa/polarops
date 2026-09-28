from pathlib import Path
import csv, math, textwrap
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.lines import Line2D

ROOT=Path("/home/prem-macharla/polarops")
CSV=ROOT/"data/Facilities_Nov2024.csv"
OUT=ROOT/"Research-stations-map-updated-2024.pdf"

with CSV.open(encoding="cp1252", newline="") as f:
    rows=list(csv.DictReader(f))

def ffloat(v):
    try: return float(str(v).replace(",",""))
    except: return None

for r in rows:
    r["lat"]=ffloat(r.get("Latitude (DD)"))
    r["lon"]=ffloat(r.get("Longitude (DD)"))
    r["operator"]=(r.get("Operator (primary)") or "").strip()
    r["name"]=(r.get("English Name") or "").strip()
    r["type"]=(r.get("Type") or "Facility").strip()
    r["season"]=(r.get("Seasonality") or "").strip()
    r["status"]=(r.get("Status") or "").strip()

rows_sorted=sorted(rows,key=lambda r:(r["name"].casefold(),r["operator"].casefold()))
for i,r in enumerate(rows_sorted,1): r["map_index"]=i
index_by_record={r["Record ID#"]:r["map_index"] for r in rows_sorted}

valid=[r for r in rows_sorted if r["lat"] is not None and r["lon"] is not None and -90<=r["lat"]<=-50 and -180<=r["lon"]<=180]
anomalies=[r for r in rows_sorted if r not in valid]

plt.rcParams.update({
    "font.family":"DejaVu Sans",
    "font.size":8,
    "axes.titlesize":11,
    "axes.titleweight":"bold",
})

def header(fig,title,subtitle=None):
    fig.text(.055,.965,title,ha="left",va="top",fontsize=19,fontweight="bold",color="#0a2e55")
    if subtitle:
        fig.text(.055,.937,subtitle,ha="left",va="top",fontsize=8.5,color="#48647d")

def footer(fig,page):
    fig.text(.055,.022,"Source: COMNAP Facilities, November 2024 public dataset (Facilities_Nov2024.csv).",fontsize=6.5,color="#5b6f80")
    fig.text(.945,.022,f"PolarOps updated edition · page {page}/4",ha="right",fontsize=6.5,color="#5b6f80")

def season_marker(r):
    return "s" if r["season"].lower().startswith("year") else "o"

with PdfPages(OUT) as pdf:
    # PAGE 1 - map
    fig=plt.figure(figsize=(8.27,11.69),facecolor="white")
    header(fig,"ANTARCTICA","Updated COMNAP research stations, camps, refuges, depots, laboratories and airfield camps")
    ax=fig.add_axes([.08,.32,.84,.56],projection="polar")
    ax.set_theta_zero_location("N"); ax.set_theta_direction(-1)
    ax.set_ylim(0,36)
    ax.set_yticks([0,10,20,30]); ax.set_yticklabels(["90°S","80°S","70°S","60°S"],fontsize=6.5,color="#597189")
    ax.set_xticks([math.radians(x) for x in range(0,360,30)])
    ax.set_xticklabels([f"{x}°" for x in range(0,360,30)],fontsize=6,color="#7890a3")
    ax.grid(color="#b9d0df",linewidth=.55)
    ax.set_facecolor("#eef7fb")
    # subtle polar-cap rings
    for rr,alpha in [(10,.08),(20,.05),(30,.03)]:
        ax.fill_between([0,2*math.pi],[0,0],[rr,rr],color="#4ba3c7",alpha=alpha,zorder=0)
    for r in valid:
        th=math.radians((r["lon"]+360)%360); rad=90+r["lat"]
        year=r["season"].lower().startswith("year")
        closed="closed" in r["status"].lower()
        marker="s" if year else "o"
        face="#0b6fb8" if not closed else "white"
        edge="#0b6fb8" if not closed else "#d04b3e"
        ax.scatter([th],[rad],s=18 if year else 15,marker=marker,c=face,edgecolors=edge,linewidths=.8,zorder=3)
        # label all points; inset handles dense peninsula
        if not (-80<=r["lon"]<=-40 and r["lat"]>=-76):
            ax.text(th,rad+0.75,str(r["map_index"]),fontsize=4.4,ha="center",va="center",color="#17354d",zorder=4)

    ax.set_title("South-polar facility map (index numbers correspond to pages 2-4)",pad=16,color="#123c60")

    # Peninsula inset
    ins=fig.add_axes([.08,.08,.45,.20])
    pen=[r for r in valid if -80<=r["lon"]<=-40 and r["lat"]>=-76]
    ins.set_xlim(-80,-40); ins.set_ylim(-76,-58); ins.set_facecolor("#f5fbfe")
    ins.grid(color="#d8e7ef",linewidth=.5)
    ins.set_xlabel("Longitude",fontsize=6); ins.set_ylabel("Latitude",fontsize=6)
    ins.set_title("Antarctic Peninsula inset",fontsize=9,fontweight="bold",loc="left")
    ins.tick_params(labelsize=5.5)
    for r in pen:
        year=r["season"].lower().startswith("year")
        closed="closed" in r["status"].lower()
        ins.scatter(r["lon"],r["lat"],s=18 if year else 14,marker="s" if year else "o",
                    c="#0b6fb8" if not closed else "white",
                    edgecolors="#0b6fb8" if not closed else "#d04b3e",linewidths=.7,zorder=3)
        ins.text(r["lon"]+.35,r["lat"]+.15,str(r["map_index"]),fontsize=4.5,color="#17354d",zorder=4)

    # Summary block / legend
    bx=fig.add_axes([.56,.075,.36,.205]); bx.axis("off")
    stats=[
        ("Facilities",len(rows_sorted)),
        ("Operators / countries",len(set(r["operator"] for r in rows_sorted if r["operator"]))),
        ("Stations",sum(r["type"]=="Station" for r in rows_sorted)),
        ("Year-round",sum(r["season"]=="Year-Round" for r in rows_sorted)),
        ("Seasonal",sum(r["season"]=="Seasonal" for r in rows_sorted)),
        ("Open",sum(r["status"]=="Open" for r in rows_sorted)),
    ]
    bx.text(0,1,"2024 DIRECTORY SUMMARY",fontsize=8,fontweight="bold",color="#0a6fae",va="top")
    y=.85
    for k,v in stats:
        bx.text(0,y,k,fontsize=7,color="#51697c")
        bx.text(.62,y,str(v),fontsize=11,fontweight="bold",color="#153b5b",ha="right")
        y-=.13
    legend=[
      Line2D([0],[0],marker="s",color="none",markerfacecolor="#0b6fb8",markeredgecolor="#0b6fb8",markersize=6,label="Year-round"),
      Line2D([0],[0],marker="o",color="none",markerfacecolor="#0b6fb8",markeredgecolor="#0b6fb8",markersize=6,label="Seasonal"),
      Line2D([0],[0],marker="o",color="none",markerfacecolor="white",markeredgecolor="#d04b3e",markersize=6,label="Temporarily closed"),
    ]
    bx.legend(handles=legend,loc="lower left",bbox_to_anchor=(-.03,-.18),frameon=False,fontsize=6.8,ncol=1)
    if anomalies:
        names=", ".join(r["name"] for r in anomalies)
        fig.text(.56,.045,f"Coordinate note: {names} is listed in the source but omitted from the map because its published latitude falls outside the Antarctic range.",fontsize=5.7,color="#8a4d38",wrap=True)
    footer(fig,1); pdf.savefig(fig,bbox_inches="tight"); plt.close(fig)

    def list_page(title, subtitle, items, page, columns=3):
        fig=plt.figure(figsize=(8.27,11.69),facecolor="white")
        header(fig,title,subtitle)
        footer(fig,page)
        left=.055; right=.955; top=.90; bottom=.055
        colw=(right-left)/columns
        per=math.ceil(len(items)/columns)
        lineh=(top-bottom)/(per+1)
        for c in range(columns):
            start=c*per; end=min(len(items),(c+1)*per)
            x=left+c*colw
            for j,item in enumerate(items[start:end]):
                y=top-j*lineh
                fig.text(x,y,item,fontsize=6.55,ha="left",va="top",color="#18364f")
        pdf.savefig(fig,bbox_inches="tight"); plt.close(fig)

    by_index=[]
    for r in rows_sorted:
        seas="YR" if r["season"]=="Year-Round" else "S"
        stat="Open" if r["status"]=="Open" else "Temp. closed"
        by_index.append(f'{r["map_index"]:>3}  {r["name"][:27]:<27}  {r["operator"][:14]:<14}  {r["type"][:10]} · {seas} · {stat}')
    list_page("Facilities by map index","Map index is assigned alphabetically in this updated edition; COMNAP Record ID remains the source identifier.",by_index,2)

    country_items=[]
    grouped={}
    for r in rows_sorted: grouped.setdefault(r["operator"] or "Unspecified",[]).append(r)
    for country in sorted(grouped,key=str.casefold):
        country_items.append(country.upper())
        for r in sorted(grouped[country],key=lambda x:x["name"].casefold()):
            seas="YR" if r["season"]=="Year-Round" else "S"
            country_items.append(f'  {r["map_index"]:>3}  {r["name"][:30]} [{r["type"][:9]}, {seas}]')
    list_page("Facilities by operator / country","Current public COMNAP facility directory grouped by primary operator.",country_items,3)

    by_name=[]
    for r in rows_sorted:
        seas="YR" if r["season"]=="Year-Round" else "S"
        record=r.get("Record ID#","")
        by_name.append(f'{r["name"][:31]:<31} {r["operator"][:15]:<15} #{r["map_index"]:03d} · COMNAP {record} · {r["type"][:9]} · {seas}')
    list_page("Facilities by name","Alphabetical facility list with PolarOps map index and COMNAP source Record ID.",by_name,4)

print(OUT)
print("rows",len(rows_sorted),"mapped",len(valid),"anomalies",[(r["name"],r["lat"]) for r in anomalies])
