"""Offline construction, size variations and explicit evidence review."""
import copy
import hashlib
import json
import math
from xml.sax.saxutils import escape

from .recipes import recipe_number, recipe_polygon
from .operations import _integer


def matched_sleeve_cap(armhole_length, bicep_width, cuff_width, sleeve_length,
                       minimum_cap_height, maximum_cap_height, ease_percent=5.0,
                       segments=64, tolerance=0.1, vertical_direction='down'):
    armhole,width,cuff,length,low,high = [recipe_number(v,n,True) for v,n in zip(
        (armhole_length,bicep_width,cuff_width,sleeve_length,minimum_cap_height,maximum_cap_height),
        ('armhole_length','bicep_width','cuff_width','sleeve_length','minimum_cap_height','maximum_cap_height'))]
    ease,tolerance = recipe_number(ease_percent,'ease_percent'),recipe_number(tolerance,'tolerance',True)
    _integer(segments,'segments',minimum=16,maximum=128)
    if not 0<=ease<=30 or not 0<low<high<length:
        raise ValueError('Require ease 0..30 and 0 < min cap height < max cap height < sleeve length')
    if vertical_direction not in ('up','down'):
        raise ValueError('vertical_direction must be up or down')
    sign = 1 if vertical_direction=='down' else -1
    def curve(height):
        return [[-width/2+width*i/segments,sign*4*height*(i/segments)*(1-i/segments)] for i in range(segments+1)]
    def arc(height):
        points = curve(height)
        return sum(math.dist(a,b) for a,b in zip(points,points[1:]))
    target = armhole*(1+ease/100)
    bracket = [arc(low),arc(high)]
    if not bracket[0]<=target<=bracket[1]:
        raise ValueError('Target cap length lies outside the supplied height bounds; change width or cap bounds')
    for _ in range(64):
        middle = (low+high)/2
        measured = arc(middle)
        if abs(measured-target)<=tolerance:
            break
        if measured<target:
            low=middle
        else:
            high=middle
    cap=curve(middle)
    outline=cap+[[cuff/2,-sign*(length-middle)],[-cuff/2,-sign*(length-middle)]]
    outline=[list(p) for p in recipe_polygon(outline)]
    return {'ok':True,'points':outline,'cap_points':cap,'cap_height':middle,
            'armhole_length':armhole,'target_cap_length':target,'draft_cap_length':measured,
            'length_error':measured-target,'tolerance_met':abs(measured-target)<=tolerance,
            'cap_draft_edge_indices':list(range(segments)),'cuff_draft_edge_index':segments+1,
            'cap_curve':{'kind':'quadratic_bezier','start':cap[0],
                         'control':[0,sign*2*middle],'end':cap[-1]},
            'native_edge_indices_verified':False,'fit_certified':False,
            'scope':'length-matched segmented sleeve draft; inspect native boundaries before sewing; no fitted shoulder or notch inference'}


def size_variants(points, sizes, pivot=None):
    points=recipe_polygon(points)
    if not isinstance(sizes,list) or not 1<=len(sizes)<=20:
        raise ValueError('Provide 1..20 explicit size records')
    pivot = [0.0,0.0] if pivot is None else pivot
    if not isinstance(pivot,list) or len(pivot)!=2:
        raise ValueError('pivot must be [x,y]')
    px,py=[recipe_number(v,'pivot') for v in pivot]
    names=set()
    variants=[]
    for size in sizes:
        if not isinstance(size,dict) or set(size)!={'name','scale_x','scale_y'}:
            raise ValueError('Every size requires exactly name, scale_x, scale_y')
        name=size['name']
        if not isinstance(name,str) or not name.strip() or len(name)>100 or name in names:
            raise ValueError('Size names must be unique nonempty strings up to 100 characters')
        names.add(name)
        sx,sy=[recipe_number(size[k],k,True) for k in ('scale_x','scale_y')]
        if not 0.5<=sx<=2 or not 0.5<=sy<=2:
            raise ValueError('Size scales must be within 0.5..2')
        draft=[list(p) for p in recipe_polygon([[px+(x-px)*sx,py+(y-py)*sy] for x,y in points])]
        variants.append({'name':name,'points':draft,'scale_x':sx,'scale_y':sy,
                         'boundary_lengths':[math.dist(p,draft[(i+1)%len(draft)]) for i,p in enumerate(draft)]})
    return {'ok':True,'variants':variants,'native_grading_rules_created':False,
            'scope':'explicit affine polygon size drafts; seam balance, darts, ease and body fit require review per size'}


def construction_svg(stitch_outline, cutting_outline=None, markers=None, units_per_mm=1.0):
    stitch=[list(p) for p in recipe_polygon(stitch_outline)]
    cutting=[list(p) for p in recipe_polygon(cutting_outline)] if cutting_outline else None
    scale=recipe_number(units_per_mm,'units_per_mm',True)
    markers=[] if markers is None else markers
    if not isinstance(markers,list) or len(markers)>200:
        raise ValueError('Provide at most 200 markers')
    clean=[]
    for item in markers:
        if not isinstance(item,dict) or set(item)!={'label','position'}:
            raise ValueError('Marker requires label and position')
        label,position=item['label'],item['position']
        if not isinstance(label,str) or len(label)>100 or not isinstance(position,list) or len(position)!=2:
            raise ValueError('Invalid marker label/position')
        clean.append((label,[recipe_number(v,'marker position') for v in position]))
    allpoints=stitch+(cutting or [])+[p for _,p in clean]
    left,bottom=min(p[0] for p in allpoints),min(p[1] for p in allpoints)
    right,top=max(p[0] for p in allpoints),max(p[1] for p in allpoints)
    # Native MD Y goes up; SVG Y goes down. Add physical 10 mm margin.
    margin=10*scale
    width,height=(right-left+2*margin)/scale,(top-bottom+2*margin)/scale
    def point(p):
        return ((p[0]-left+margin)/scale,(top-p[1]+margin)/scale)
    def polygon(points,color,dash=''):
        pairs=' '.join('%g,%g'%point(p) for p in points)
        return '<polygon points="%s" fill="none" stroke="%s" stroke-width="0.3" %s/>'%(pairs,color,dash)
    elements=[polygon(stitch,'#2066a8','stroke-dasharray="2 1"')]
    if cutting:
        elements.append(polygon(cutting,'#111111'))
    for label,p in clean:
        x,y=point(p)
        elements.append('<circle cx="%g" cy="%g" r="1" fill="none" stroke="#b03030" stroke-width="0.3"/>'%(x,y))
        elements.append('<text x="%g" y="%g" font-size="3">%s</text>'%(x+2,y,escape(label)))
    svg='<svg xmlns="http://www.w3.org/2000/svg" width="%gmm" height="%gmm" viewBox="0 0 %g %g">%s</svg>'%(width,height,width,height,''.join(elements))
    return svg,{'ok':True,'width_mm':width,'height_mm':height,'units_per_mm':scale,
                'scope':'1:1 SVG draft; print at 100 percent and verify physical scale; no native accessory or allowance property created'}


def migration_plan(before,after):
    def pieces(document):
        if not isinstance(document,dict) or not isinstance(document.get('PatternList'),list):
            raise ValueError('Supply native PatternList exports')
        result=[]
        identities=set()
        for p in document['PatternList']:
            if not isinstance(p,dict) or not isinstance(p.get('ID'),str) or not p['ID'] or p['ID'] in identities:
                raise ValueError('Native pattern IDs must be unique nonempty strings')
            if not isinstance(p.get('ShapeInfo'),dict) or not p['ShapeInfo'].get('LineList'):
                raise ValueError('Native pieces must contain boundary geometry')
            identities.add(p['ID'])
            def strip(node):
                if isinstance(node,list):
                    return [strip(v) for v in node]
                if isinstance(node,dict):
                    return {k:strip(v) for k,v in node.items() if k!='ID'}
                return node
            geometry=strip({'shape':p['ShapeInfo'],'internal':p.get('InternalLineList',[])})
            fingerprint=hashlib.sha256(json.dumps(geometry,sort_keys=True,allow_nan=False).encode()).hexdigest()
            result.append({'id':p['ID'],'name':p.get('Name'),'geometry_hash':fingerprint})
        return result
    first,second=pieces(before),pieces(after)
    proposals=[]
    for old in first:
        matches=[p for p in second if p['name']==old['name'] and p['geometry_hash']==old['geometry_hash']]
        proposals.append({'before':old,'status':'candidate' if len(matches)==1 else 'ambiguous' if matches else 'changed_or_missing',
                          'candidates':matches,'requires_native_api_edge_inspection':True})
    proposed=[p['candidates'][0]['id'] for p in proposals if p['status']=='candidate']
    if len(set(proposed))!=len(proposed):
        for p in proposals:
            if p['status']=='candidate' and proposed.count(p['candidates'][0]['id'])>1:
                p['status']='ambiguous'
    return {'ok':True,'proposals':proposals,'registry_modified':False,'native_id_persistence_certified':False,
            'scope':'unique name plus exact ID-stripped exported geometry; explicit native inspection/rebinding still required'}


def assess_evidence(checks):
    required={'placement','sewing','clearance','deformation','appearance','recovery'}
    if not isinstance(checks,list) or not 1<=len(checks)<=50:
        raise ValueError('Provide 1..50 evidence checks')
    records=[]
    names=set()
    for c in checks:
        if not isinstance(c,dict) or set(c)!={'category','name','status','evidence','note'}:
            raise ValueError('Each check requires category, name, status, evidence, note')
        if c['category'] not in required or c['status'] not in ('pass','fail','unverified'):
            raise ValueError('Unrecognized evidence category/status')
        if any(not isinstance(c[k],str) or len(c[k])>2000 for k in ('name','evidence','note')) or not c['name'].strip():
            raise ValueError('Supply bounded textual name/evidence/note')
        if c['name'] in names:
            raise ValueError('Check names must be unique')
        if c['status']=='pass' and not c['evidence'].strip():
            raise ValueError('Pass claims require an explicit evidence reference')
        names.add(c['name'])
        records.append(copy.deepcopy(c))
    missing=sorted(required-{c['category'] for c in records})
    gaps=[c['name'] for c in records if c['status']!='pass']
    return {'ok':True,'checks':records,'missing_categories':missing,'unresolved_checks':gaps,
            'review_status':'needs_review' if missing or gaps else 'caller_evidence_complete',
            'fit_certified':False,'evidence_independently_verified':False,
            'scope':'organizes caller observations; does not run tests, inspect referenced files or certify garment quality'}
