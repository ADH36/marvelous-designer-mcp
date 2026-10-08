"""Local pattern drafts and edits; no invented native accessory schema."""
import copy
import math
from .recipes import recipe_polygon, recipe_number
from .operations import _integer


def area(points):
    return sum(points[i][0]*points[(i+1)%len(points)][1]-points[(i+1)%len(points)][0]*points[i][1]
               for i in range(len(points)))/2


def draft_dart(points,edge_index,intake,depth,position_ratio=0.5):
    polygon=recipe_polygon(points)
    _integer(edge_index,'edge_index',maximum=len(polygon)-1)
    intake,depth=recipe_number(intake,'intake',True),recipe_number(depth,'depth',True)
    ratio=recipe_number(position_ratio,'position_ratio')
    a,b=polygon[edge_index],polygon[(edge_index+1)%len(polygon)]
    dx,dy=b[0]-a[0],b[1]-a[1]
    length=math.hypot(dx,dy)
    center=length*ratio
    if not intake/2<center<length-intake/2:
        raise ValueError('Dart intake must fit strictly inside the selected boundary edge')
    ux,uy=dx/length,dy/length
    sign=1 if area(polygon)>0 else -1
    nx,ny=-uy*sign,ux*sign
    left=[a[0]+ux*(center-intake/2),a[1]+uy*(center-intake/2)]
    right=[a[0]+ux*(center+intake/2),a[1]+uy*(center+intake/2)]
    tip=[a[0]+ux*center+nx*depth,a[1]+uy*center+ny*depth]
    inside=False
    for i,p in enumerate(polygon):
        q=polygon[(i+1)%len(polygon)]
        if (p[1]>tip[1])!=(q[1]>tip[1]) and tip[0]<(q[0]-p[0])*(tip[1]-p[1])/(q[1]-p[1])+p[0]:
            inside=not inside
    if not inside:
        raise ValueError('Dart tip must lie inside the original polygon')
    result=[list(p) for p in polygon[:edge_index+1]]+[left,tip,right]+[list(p) for p in polygon[edge_index+1:]]
    result=[list(p) for p in recipe_polygon(result)]
    return {'ok':True,'points':result,'dart':{'left':left,'tip':tip,'right':right,
            'intake':intake,'depth':depth,'leg_length':math.hypot(depth,intake/2)},
            'draft_leg_indices':[edge_index+1,edge_index+2],
            'sewing_directions':[True,False],'native_edge_indices_verified':False,
            'scope':'straight polygon cut-out dart; create a new pattern and inspect actual edges before sewing; not a native internal dart'}


def seam_allowance(points,width,miter_limit=5.0):
    polygon=recipe_polygon(points)
    width=recipe_number(width,'width',True)
    limit=recipe_number(miter_limit,'miter_limit',True)
    sign=1 if area(polygon)>0 else -1
    shifted=[]
    for i,p in enumerate(polygon):
        q=polygon[(i+1)%len(polygon)]
        dx,dy=q[0]-p[0],q[1]-p[1]
        length=math.hypot(dx,dy)
        normal=(dy/length*sign,-dx/length*sign)
        shifted.append(((p[0]+width*normal[0],p[1]+width*normal[1]),(dx/length,dy/length),normal))
    cutting=[]
    for i,p in enumerate(polygon):
        a,u,previous_normal=shifted[(i-1)%len(polygon)]
        b,v,normal=shifted[i]
        determinant=u[0]*v[1]-u[1]*v[0]
        if abs(determinant)<1e-10:
            if u[0]*v[0]+u[1]*v[1]<0:
                raise ValueError('Reversed adjacent edges cannot be offset')
            point=[p[0]+width*normal[0],p[1]+width*normal[1]]
        else:
            delta=(b[0]-a[0],b[1]-a[1])
            distance=(delta[0]*v[1]-delta[1]*v[0])/determinant
            point=[a[0]+distance*u[0],a[1]+distance*u[1]]
        if math.dist(point,p)>width*limit:
            raise ValueError('Miter exceeds the limit; reduce allowance or edit the acute corner')
        cutting.append(point)
    cutting=[list(p) for p in recipe_polygon(cutting)]
    if abs(area(cutting))<=abs(area(polygon)):
        raise ValueError('Offset did not produce a larger cutting outline')
    return {'ok':True,'stitch_outline':[list(p) for p in polygon],'cutting_outline':cutting,
            'width':width,'miter_limit':limit,'native_seam_allowance_created':False,
            'scope':'local straight-polygon cutting draft; concave/self-intersecting offsets are rejected, not silently repaired'}


def closure_layout(start,end,placket_width,spacing,end_margin,kind='buttons'):
    if kind not in ('buttons','zipper'):
        raise ValueError('kind must be buttons or zipper')
    if not isinstance(start,list) or not isinstance(end,list) or len(start)!=2 or len(end)!=2:
        raise ValueError('start/end must be [x,y]')
    start,end=[recipe_number(v,'start') for v in start],[recipe_number(v,'end') for v in end]
    width=recipe_number(placket_width,'placket_width',True)
    spacing=recipe_number(spacing,'spacing',True)
    margin=recipe_number(end_margin,'end_margin')
    dx,dy=end[0]-start[0],end[1]-start[1]
    length=math.hypot(dx,dy)
    if margin<0 or length<=2*margin:
        raise ValueError('Closure length must exceed twice the nonnegative end margin')
    ux,uy=dx/length,dy/length
    nx,ny=-uy*width/2,ux*width/2
    outline=[[start[0]+nx,start[1]+ny],[end[0]+nx,end[1]+ny],
             [end[0]-nx,end[1]-ny],[start[0]-nx,start[1]-ny]]
    positions=[]
    if kind=='buttons':
        count=math.floor((length-2*margin)/spacing)+1
        if count>200:
            raise ValueError('Closure layout exceeds 200 button positions')
        offset=(length-(count-1)*spacing)/2
        positions=[[start[0]+ux*(offset+i*spacing),start[1]+uy*(offset+i*spacing)] for i in range(count)]
    return {'ok':True,'kind':kind,'placket_points':[list(p) for p in recipe_polygon(outline)],
            'centerline':[start,end],'length':length,'button_centers':positions,
            'native_accessories_created':False,'scope':'local closure layout and placket draft; does not invent zipper/button placement API'}


def transform_native_document(document,pattern_ids,scale_x=1.0,scale_y=1.0,rotation_degrees=0.0,
                              translation_x=0.0,translation_y=0.0,pivot=None):
    if not isinstance(document,dict) or not isinstance(document.get('PatternList'),list):
        raise ValueError('Supply an exported MD native PatternList document')
    if not isinstance(pattern_ids,list) or not pattern_ids or any(not isinstance(i,str) or not i for i in pattern_ids):
        raise ValueError('Provide explicit native pattern IDs from this exported document')
    if len(set(pattern_ids))!=len(pattern_ids):
        raise ValueError('Duplicate selected pattern ID')
    result=copy.deepcopy(document)
    selected=[]
    for identity in pattern_ids:
        matches=[p for p in result['PatternList'] if isinstance(p,dict) and p.get('ID')==identity]
        if len(matches)!=1:
            raise ValueError('Native pattern ID is missing or ambiguous')
        selected.extend(matches)
    sx,sy=recipe_number(scale_x,'scale_x',True),recipe_number(scale_y,'scale_y',True)
    angle=math.radians(recipe_number(rotation_degrees,'rotation_degrees'))
    dx,dy=recipe_number(translation_x,'translation_x'),recipe_number(translation_y,'translation_y')
    pivot=[0.0,0.0] if pivot is None else pivot
    if not isinstance(pivot,list) or len(pivot)!=2:
        raise ValueError('pivot must be [x,y]')
    px,py=(recipe_number(v,'pivot') for v in pivot)
    changed=0
    positions={}
    def walk(node):
        nonlocal changed
        if isinstance(node,list):
            for child in node:
                walk(child)
        elif isinstance(node,dict):
            position=node.get('Position')
            if isinstance(position,dict) and set(position)=={'x','y'}:
                x,y=recipe_number(position['x'],'x'),recipe_number(position['y'],'y')
                identity=node.get('ID')
                if identity is not None:
                    if identity in positions and positions[identity]!=(x,y):
                        raise ValueError('Shared native point ID has inconsistent coordinates')
                    positions[identity]=(x,y)
                x,y=(x-px)*sx,(y-py)*sy
                position['x']=recipe_number(px+x*math.cos(angle)-y*math.sin(angle)+dx,'transformed x')
                position['y']=recipe_number(py+x*math.sin(angle)+y*math.cos(angle)+dy,'transformed y')
                changed+=1
            for key,child in node.items():
                if key!='Position':
                    walk(child)
    for piece in selected:
        # Sewing length parameters and grading rules may cease to correspond after nonuniform transforms.
        if document.get('GradingRuleTableList'):
            raise ValueError('Graded documents require native grading support; affine editing is disabled')
        walk(piece)
    if not changed:
        raise ValueError('No recognized native XY Position records to transform')
    return {'ok':True,'document':result,'changed_position_records':changed,'pattern_ids':pattern_ids,
            'refresh_indices':True,'rebind_references':True,
            'limits':['2D position records only; not 3D avatar placement','sewing length ratios must be reinspected after scaling',
                      'native IDs are scoped to this file, not guaranteed across imports','accessory dimensions and grading rules are not resized']}
