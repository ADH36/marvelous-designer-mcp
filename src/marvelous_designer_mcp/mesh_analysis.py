"""Offline geometric evidence, not MD pressure/strain/collision sensor values."""
import heapq
import math
import time
import hashlib
from pathlib import Path
from xml.sax.saxutils import escape
from collections import Counter

from .operations import _path, _integer
from .recipes import recipe_number


def sub(a,b):
    return tuple(x-y for x,y in zip(a,b))


def dot(a,b):
    return sum(x*y for x,y in zip(a,b))


def cross(a,b):
    return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])


def add_scaled(a,b,t):
    return tuple(x+t*y for x,y in zip(a,b))


def read_triangles(path):
    vertices,triangles,_=read_triangle_records(path)
    return vertices,triangles


def read_triangle_records(path):
    path=_path(path,'.obj',must_exist=True)
    vertices,triangles=[],[]
    labels=[]
    label={'object':None,'group':None,'material':None}
    with open(path,encoding='utf-8') as file:
        for line in file:
            fields=line.split('#',1)[0].split()
            if not fields:
                continue
            if fields[0] in ('o','g','usemtl'):
                label[{'o':'object','g':'group','usemtl':'material'}[fields[0]]]=' '.join(fields[1:]) or None
            if fields[0]=='v':
                if len(fields)<4:
                    raise ValueError('Incomplete OBJ vertex')
                vertex=tuple(float(x) for x in fields[1:4])
                if not all(math.isfinite(x) and abs(x)<=10_000_000 for x in vertex):
                    raise ValueError('Nonfinite or out-of-range OBJ vertex')
                vertices.append(vertex)
                if len(vertices)>1_000_000:
                    raise ValueError('Mesh exceeds one million vertices')
            elif fields[0]=='f':
                if len(fields)!=4:
                    raise ValueError('Analysis requires a triangulated OBJ; do not infer triangulation of concave polygons')
                face=[]
                for value in fields[1:]:
                    index=int(value.split('/')[0])
                    if index==0:
                        raise ValueError('OBJ indices are one-based; zero is invalid')
                    index=index-1 if index>0 else len(vertices)+index
                    if not 0<=index<len(vertices):
                        raise ValueError('OBJ face references an unavailable vertex')
                    face.append(index)
                triangles.append(tuple(face))
                labels.append(dict(label))
                if len(triangles)>2_000_000:
                    raise ValueError('Mesh exceeds two million faces')
    if not vertices or not triangles:
        raise ValueError('Mesh contains no triangle geometry')
    return vertices,triangles,labels


def closest_point(p,a,b,c):
    # Voronoi-region point/triangle projection (Ericson).
    ab,ac,ap=sub(b,a),sub(c,a),sub(p,a)
    d1,d2=dot(ab,ap),dot(ac,ap)
    if d1<=0 and d2<=0:
        return a
    bp=sub(p,b)
    d3,d4=dot(ab,bp),dot(ac,bp)
    if d3>=0 and d4<=d3:
        return b
    vc=d1*d4-d3*d2
    if vc<=0 and d1>=0 and d3<=0:
        return add_scaled(a,ab,d1/(d1-d3))
    cp=sub(p,c)
    d5,d6=dot(ab,cp),dot(ac,cp)
    if d6>=0 and d5<=d6:
        return c
    vb=d5*d2-d1*d6
    if vb<=0 and d2>=0 and d6<=0:
        return add_scaled(a,ac,d2/(d2-d6))
    va=d3*d6-d5*d4
    if va<=0 and d4-d3>=0 and d5-d6>=0:
        return add_scaled(b,sub(c,b),(d4-d3)/((d4-d3)+(d5-d6)))
    denominator=va+vb+vc
    if abs(denominator)<1e-20:
        raise ValueError('Degenerate triangle')
    return add_scaled(add_scaled(a,ab,vb/denominator),ac,vc/denominator)


class TriangleTree:
    def __init__(self,vertices,faces):
        self.vertices,self.faces=vertices,faces
        self.boxes=[]
        valid=[]
        self.degenerate=0
        for i,face in enumerate(faces):
            pts=[vertices[j] for j in face]
            area_vector=cross(sub(pts[1],pts[0]),sub(pts[2],pts[0]))
            if dot(area_vector,area_vector)<1e-20:
                self.degenerate+=1
                self.boxes.append(None)
                continue
            self.boxes.append((tuple(min(p[a] for p in pts) for a in range(3)),
                               tuple(max(p[a] for p in pts) for a in range(3))))
            valid.append(i)
        if not valid:
            raise ValueError('Avatar has no nondegenerate triangles')
        self.nodes=[]
        self.root=self.build(valid)

    def build(self,indices):
        lo=tuple(min(self.boxes[i][0][a] for i in indices) for a in range(3))
        hi=tuple(max(self.boxes[i][1][a] for i in indices) for a in range(3))
        node=len(self.nodes)
        self.nodes.append(None)
        if len(indices)<=12:
            self.nodes[node]=(lo,hi,None,None,indices)
        else:
            axis=max(range(3),key=lambda a:hi[a]-lo[a])
            indices.sort(key=lambda i:sum(self.boxes[i][b][axis] for b in (0,1)))
            middle=len(indices)//2
            left,right=self.build(indices[:middle]),self.build(indices[middle:])
            self.nodes[node]=(lo,hi,left,right,None)
        return node

    @staticmethod
    def box_distance(p,lo,hi):
        return sum(max(lo[a]-p[a],0,p[a]-hi[a])**2 for a in range(3))

    def nearest(self,p):
        best=float('inf')
        best_face,best_point=None,None
        queue=[(0,self.root)]
        while queue:
            bound,index=heapq.heappop(queue)
            if bound>best:
                continue
            lo,hi,left,right,faces=self.nodes[index]
            if faces is not None:
                for face in faces:
                    point=closest_point(p,*(self.vertices[i] for i in self.faces[face]))
                    delta=sub(p,point)
                    distance=dot(delta,delta)
                    if distance<best:
                        best,best_face,best_point=distance,face,point
            else:
                for child in (left,right):
                    lo,hi,*_=self.nodes[child]
                    distance=self.box_distance(p,lo,hi)
                    if distance<=best:
                        heapq.heappush(queue,(distance,child))
        return math.sqrt(best),best_face,best_point

    @staticmethod
    def ray_box(p,direction,lo,hi):
        start,end=0,float('inf')
        for a in range(3):
            if abs(direction[a])<1e-12:
                if not lo[a]<=p[a]<=hi[a]:
                    return False
            else:
                t1,t2=(lo[a]-p[a])/direction[a],(hi[a]-p[a])/direction[a]
                start,end=max(start,min(t1,t2)),min(end,max(t1,t2))
                if start>end:
                    return False
        return True

    def parity(self,p,direction):
        count=0
        stack=[self.root]
        while stack:
            index=stack.pop()
            lo,hi,left,right,faces=self.nodes[index]
            if not self.ray_box(p,direction,lo,hi):
                continue
            if faces is None:
                stack.extend((left,right))
                continue
            for face in faces:
                a,b,c=(self.vertices[i] for i in self.faces[face])
                e1,e2=sub(b,a),sub(c,a)
                h=cross(direction,e2)
                determinant=dot(e1,h)
                if abs(determinant)<1e-12:
                    continue
                inverse=1/determinant
                s=sub(p,a)
                u=dot(s,h)*inverse
                if not 0<=u<=1:
                    continue
                q=cross(s,e1)
                v=dot(direction,q)*inverse
                if v<0 or u+v>1:
                    continue
                t=dot(e2,q)*inverse
                if t>1e-8:
                    # Boundary intersections are ambiguous; do not double count.
                    if min(u,v,1-u-v)<1e-9:
                        return None
                    count+=1
        return bool(count%2)


def analyze_clearance(garment_path,avatar_path,clearance=2.0,max_samples=2000):
    clearance=recipe_number(clearance,'clearance',True)
    _integer(max_samples,'max_samples',minimum=10,maximum=10000)
    garment,gfaces=read_triangles(garment_path)
    avatar,afaces=read_triangles(avatar_path)
    tree=TriangleTree(avatar,afaces)
    edges=Counter(tuple(sorted((face[i],face[(i+1)%3]))) for face in afaces for i in range(3))
    closed=all(count==2 for count in edges.values()) and tree.degenerate==0
    # Even spacing with endpoints included; triangle-interior intersections are not sampled.
    count=min(len(garment),max_samples)
    indices=sorted({round(i*(len(garment)-1)/max(count-1,1)) for i in range(count)})
    worst=[]
    counts=Counter()
    minimum=float('inf')
    for index in indices:
        point=garment[index]
        distance,face,nearest=tree.nearest(point)
        minimum=min(minimum,distance)
        inside=None
        if closed and distance>1e-7:
            first=tree.parity(point,(1.0,0.371,0.217))
            second=tree.parity(point,(0.193,1.0,0.413))
            if first is not None and first==second:
                inside=first
        state='inside_candidate' if inside is True else 'near_surface' if distance<clearance else 'outside' if inside is False else 'unsigned_only'
        counts[state]+=1
        if state in ('inside_candidate','near_surface'):
            worst.append({'vertex_index':index,'position':point,'distance':distance,
                          'signed_distance':-distance if inside is True else distance if inside is False else None,
                          'classification':state,'avatar_triangle':face,'nearest_surface_point':nearest})
    worst.sort(key=lambda item:(item['classification']!='inside_candidate',
                               -item['distance'] if item['classification']=='inside_candidate' else item['distance']))
    return {'ok':True,'garment_path':garment_path,'avatar_path':avatar_path,
            'garment_vertices':len(garment),'garment_triangles':len(gfaces),'avatar_triangles':len(afaces),
            'sample_count':len(indices),'all_garment_vertices_sampled':len(indices)==len(garment),
            'clearance_threshold':clearance,'minimum_sampled_distance':minimum,
            'avatar_closed_manifold_by_edge_count':closed,'avatar_degenerate_triangles':tree.degenerate,
            'classification_counts':dict(counts),'worst_samples':worst[:25],
            'fit_certified':False,'native_collision_sensor':False,
            'limits':['same coordinate system and unit scale required','vertex sampling can miss triangle intersections',
                      'open or ambiguous avatars provide unsigned clearance only','avatar self-intersections were not certified',
                      'no MD collision thickness, pressure or cloth physics inferred']}


def analyze_deformation(rest_path,current_path,stretch_limit_percent=10.0):
    limit=recipe_number(stretch_limit_percent,'stretch_limit_percent',True)
    rest,rfaces=read_triangles(rest_path)
    current,cfaces=read_triangles(current_path)
    if len(rest)!=len(current) or rfaces!=cfaces:
        raise ValueError('Deformation requires identical vertex counts and ordered triangle connectivity')
    edges=sorted({tuple(sorted((face[i],face[(i+1)%3]))) for face in rfaces for i in range(3)})
    worst=[]
    values=[]
    degenerate=0
    for a,b in edges:
        delta=sub(rest[a],rest[b])
        baseline=math.sqrt(dot(delta,delta))
        if baseline<1e-8:
            degenerate+=1
            continue
        delta=sub(current[a],current[b])
        length=math.sqrt(dot(delta,delta))
        change=(length/baseline-1)*100
        values.append(change)
        if abs(change)>limit:
            worst.append({'vertices':[a,b],'rest_length':baseline,'current_length':length,'change_percent':change})
    if not values:
        raise ValueError('No nondegenerate reference edges')
    worst.sort(key=lambda item:abs(item['change_percent']),reverse=True)
    return {'ok':True,'rest_path':rest_path,'current_path':current_path,'edge_count':len(values),
            'minimum_change_percent':min(values),'maximum_change_percent':max(values),
            'mean_change_percent':sum(values)/len(values),'limit_percent':limit,
            'edges_over_limit':len(worst),'degenerate_reference_edges':degenerate,'worst_edges':worst[:25],
            'fit_certified':False,'native_strain_sensor':False,
            'scope':'geometric edge elongation relative to caller-supplied rest mesh; not MD stress, pressure or material strain',
            'correspondence':'caller must establish stable export vertex order; connectivity alone is not persistent identity'}


def _boxes_overlap(a, b, epsilon):
    return all(a[0][i] <= b[1][i] + epsilon and b[0][i] <= a[1][i] + epsilon for i in range(3))


def _triangles_overlap(first, second, epsilon):
    """Separating axes for triangles, including coplanar/touching cases."""
    aedges = [sub(first[(i+1)%3], first[i]) for i in range(3)]
    bedges = [sub(second[(i+1)%3], second[i]) for i in range(3)]
    anormal, bnormal = cross(aedges[0], aedges[1]), cross(bedges[0], bedges[1])
    axes = [anormal, bnormal]
    axes.extend(cross(a,b) for a in aedges for b in bedges)
    axes.extend(cross(anormal,e) for e in aedges)
    axes.extend(cross(bnormal,e) for e in bedges)
    for axis in axes:
        magnitude = math.sqrt(dot(axis,axis))
        if magnitude < 1e-15:
            continue
        # Translate to one local origin to reduce large-coordinate cancellation.
        av = [dot(sub(p,first[0]),axis)/magnitude for p in first]
        bv = [dot(sub(p,first[0]),axis)/magnitude for p in second]
        if max(av) < min(bv)-epsilon or max(bv) < min(av)-epsilon:
            return False
    return True


def _plane_slice(points,distances,epsilon):
    cut=[p for p,d in zip(points,distances) if abs(d)<=epsilon]
    for i in range(3):
        j=(i+1)%3
        if (distances[i]>epsilon and distances[j]<-epsilon) or (distances[j]>epsilon and distances[i]<-epsilon):
            cut.append(add_scaled(points[i],sub(points[j],points[i]),distances[i]/(distances[i]-distances[j])))
    return cut


def _coplanar_area(first,second,normal):
    axis=max(range(3),key=lambda i:abs(normal[i]))
    axes=[i for i in range(3) if i!=axis]
    origin=first[0]
    project=lambda points:[tuple(p[i]-origin[i] for i in axes) for p in points]
    polygon,clip=project(first),project(second)
    orient=lambda a,b,p:(b[0]-a[0])*(p[1]-a[1])-(b[1]-a[1])*(p[0]-a[0])
    sign=1 if orient(*clip)>=0 else -1
    for i in range(3):
        a,b=clip[i],clip[(i+1)%3]
        incoming,polygon=polygon,[]
        if not incoming:
            break
        previous=incoming[-1]
        pd=sign*orient(a,b,previous)
        for point in incoming:
            d=sign*orient(a,b,point)
            if (d>=0)!=(pd>=0):
                t=pd/(pd-d)
                polygon.append(tuple(previous[k]+t*(point[k]-previous[k]) for k in range(2)))
            if d>=0:
                polygon.append(point)
            previous,pd=point,d
    return abs(sum(polygon[i][0]*polygon[(i+1)%len(polygon)][1]-polygon[i][1]*polygon[(i+1)%len(polygon)][0]
                   for i in range(len(polygon))))/2


def classify_triangle_pair(first,second,epsilon):
    if not _triangles_overlap(first,second,epsilon):
        return None
    normals=[cross(sub(p[1],p[0]),sub(p[2],p[0])) for p in (first,second)]
    normals=[tuple(v/math.sqrt(dot(n,n)) for v in n) for n in normals]
    da=[dot(sub(p,second[0]),normals[1]) for p in first]
    db=[dot(sub(p,first[0]),normals[0]) for p in second]
    if max(abs(v) for v in da+db)<=epsilon:
        return 'coplanar_overlap' if _coplanar_area(first,second,normals[0])>epsilon**2 else 'contact'
    direction=cross(*normals)
    length=math.sqrt(dot(direction,direction))
    if length<=1e-12:
        return 'contact'
    direction=tuple(v/length for v in direction)
    a,b=_plane_slice(first,da,epsilon),_plane_slice(second,db,epsilon)
    if not a or not b:
        return 'contact'
    ai=[dot(sub(p,first[0]),direction) for p in a]
    bi=[dot(sub(p,first[0]),direction) for p in b]
    interval=min(max(ai),max(bi))-max(min(ai),min(bi))
    straddles=lambda d:min(d)<-epsilon and max(d)>epsilon
    return 'proper_crossing' if interval>epsilon and straddles(da) and straddles(db) else 'contact'


def _mesh_digest(path):
    digest=hashlib.sha256()
    with open(path,'rb') as file:
        for chunk in iter(lambda:file.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()


def analyze_intersections(garment_path, other_path='', epsilon=1e-6,
                          max_candidates=500000, time_budget_seconds=30.0,
                          known_seam_triangle_pairs=None,body_regions=None):
    epsilon = recipe_number(epsilon,'epsilon',True)
    _integer(max_candidates,'max_candidates',minimum=100,maximum=5000000)
    budget = recipe_number(time_budget_seconds,'time_budget_seconds',True)
    if budget > 60:
        raise ValueError('time_budget_seconds must not exceed 60')
    vertices, faces,labels = read_triangle_records(garment_path)
    own = TriangleTree(vertices,faces)
    self_check = not other_path
    other_vertices, other_faces,other_labels = (vertices,faces,labels) if self_check else read_triangle_records(other_path)
    other = own if self_check else TriangleTree(other_vertices,other_faces)
    seam_pairs=[] if known_seam_triangle_pairs is None else known_seam_triangle_pairs
    if not isinstance(seam_pairs,list) or len(seam_pairs)>10000 or (seam_pairs and not self_check):
        raise ValueError('Known seam pairs must be a self-check list of at most 10000 triangle index pairs')
    seams=set()
    for pair in seam_pairs:
        if not isinstance(pair,list) or len(pair)!=2:
            raise ValueError('Known seam pair must contain two triangle indices')
        i,j=[_integer(v,'seam triangle index',maximum=len(faces)-1) for v in pair]
        if i==j:
            raise ValueError('Known seam triangles must differ')
        seams.add(tuple(sorted((i,j))))
    regions=[] if body_regions is None else body_regions
    if not isinstance(regions,list) or len(regions)>50:
        raise ValueError('Provide at most 50 explicit body region bounds')
    for region in regions:
        if not isinstance(region,dict) or set(region)!={'name','min','max'} or not isinstance(region['name'],str) or not region['name']:
            raise ValueError('Body region requires name, min and max')
        for key in ('min','max'):
            if not isinstance(region[key],list) or len(region[key])!=3:
                raise ValueError('Body region bounds must have three coordinates')
            for value in region[key]:
                recipe_number(value,'region coordinate')
        if any(region['min'][i]>=region['max'][i] for i in range(3)):
            raise ValueError('Body region bounds must be ordered')
    started = time.monotonic()
    deadline = started + budget
    tested = hits = visited = 0
    examples_by_class={name:[] for name in ('proper_crossing','coplanar_overlap','contact')}
    classifications=Counter()
    suppressed=Counter()
    provenance=Counter()
    complete, reason = True, None
    for i, box in enumerate(own.boxes):
        if box is None:
            continue
        stack = [other.root]
        while stack:
            visited += 1
            if time.monotonic() > deadline:
                complete,reason = False,'time budget reached'
                break
            node = stack.pop()
            lo,hi,left,right,indices = other.nodes[node]
            if not _boxes_overlap(box,(lo,hi),epsilon):
                continue
            if indices is None:
                stack.extend((left,right))
                continue
            for j in indices:
                if self_check and j <= i:
                    continue
                if not _boxes_overlap(box,other.boxes[j],epsilon):
                    continue
                if tested >= max_candidates:
                    complete,reason = False,'candidate comparison limit reached'
                    break
                tested += 1
                first,second=[vertices[k] for k in faces[i]],[other_vertices[k] for k in other_faces[j]]
                classification=classify_triangle_pair(first,second,epsilon)
                if classification:
                    shared=bool(set(faces[i]) & set(faces[j])) if self_check else False
                    if classification=='contact' and self_check and (shared or (i,j) in seams):
                        suppressed['mesh_adjacent_contact' if shared else 'caller_seam_contact']+=1
                        continue
                    hits += 1
                    classifications[classification]+=1
                    provenance[(labels[i]['object'],labels[i]['group'],other_labels[j]['object'],other_labels[j]['group'])]+=1
                    if len(examples_by_class[classification]) < 34:
                        centroid=tuple(sum(p[a] for p in first)/3 for a in range(3))
                        examples_by_class[classification].append({'garment_triangle':i,'other_triangle':j,'classification':classification,
                                      'garment_source':labels[i],'other_source':other_labels[j],
                                      'position':centroid,'bounds':{'min':[min(p[a] for p in first+second) for a in range(3)],
                                                                  'max':[max(p[a] for p in first+second) for a in range(3)]},
                                      'body_regions':[r['name'] for r in regions if all(r['min'][a]<=centroid[a]<=r['max'][a] for a in range(3))]})
            if not complete:
                break
        if not complete:
            break
    return {'ok':complete,'analysis_complete':complete,'stop_reason':reason,
            'garment_path':garment_path,'other_path':other_path or garment_path,
            'self_intersections':self_check,'triangle_pairs_tested':tested,'bvh_nodes_visited':visited,
            'intersecting_or_touching_pairs':hits,
            'examples':[item for items in examples_by_class.values() for item in items][:100],'epsilon':epsilon,
            'classification_counts':dict(classifications),'suppressed_contact_counts':dict(suppressed),
            'review_status':'incomplete' if not complete else 'needs_review' if hits else 'no_findings_in_checked_scope',
            'known_seam_pairs_source':'caller supplied for this exact mesh; only contact is suppressed',
            'body_regions_source':'caller supplied bounds; centroid labels, not anatomical inference',
            'source_pair_counts':[{'garment_object':k[0],'garment_group':k[1],'other_object':k[2],'other_group':k[3],'pairs':v}
                                  for k,v in provenance.most_common(100)],
            'mesh_sha256':_mesh_digest(garment_path),'other_mesh_sha256':_mesh_digest(other_path or garment_path),
            'garment_degenerate_triangles':own.degenerate,'other_degenerate_triangles':other.degenerate,
            'elapsed_seconds':round(time.monotonic()-started,3),'fit_certified':False,
            'limits':['same coordinates and units required','crossings, coplanar area overlaps and contacts are geometric candidates, not physical sensors',
                      'adjacent/caller-declared sewn contacts are suppressed; crossings and coplanar area overlaps remain visible',
                      'degenerate triangles are excluded','zero pairs in incomplete analysis is not a clean result',
                      'surface intersections do not detect closed-volume containment or certify physical fit']}


def export_intersection_review(report,output_dir):
    """Draw orthographic SVG closeups of recorded triangle evidence, no MD camera inference."""
    folder=Path(_path(output_dir))
    if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
        raise ValueError('Intersection review requires a new or empty directory')
    for path,key in ((report['garment_path'],'mesh_sha256'),(report['other_path'],'other_mesh_sha256')):
        if _mesh_digest(path)!=report[key]:
            raise ValueError('Mesh changed since the intersection scan')
    vertices,faces=read_triangles(report['garment_path'])
    other,ofaces=read_triangles(report['other_path'])
    folder.mkdir(parents=True,exist_ok=True)
    files=[]
    for number,item in enumerate(report['examples'][:25]):
        a=[vertices[k] for k in faces[item['garment_triangle']]]
        b=[other[k] for k in ofaces[item['other_triangle']]]
        views=[]
        for col,(x,y,name) in enumerate(((0,1,'XY'),(0,2,'XZ'),(2,1,'ZY'))):
            lo=[min(p[i] for p in a+b) for i in (x,y)]
            hi=[max(p[i] for p in a+b) for i in (x,y)]
            scale=220/max(hi[0]-lo[0],hi[1]-lo[1],1e-6)
            for pts,color in ((a,'#0ea5e9'),(b,'#f97316')):
                points=' '.join('%g,%g'%(col*260+20+(p[x]-lo[0])*scale,270-(p[y]-lo[1])*scale) for p in pts)
                views.append('<polygon points="%s" fill="%s" fill-opacity=".25" stroke="%s"/>'%(points,color,color))
            views.append('<text x="%d" y="305">%s projection</text>'%(col*260+20,name))
        title='Pair %d/%d: %s'%(item['garment_triangle'],item['other_triangle'],item['classification'])
        path=folder/('pair_%03d.svg'%number)
        path.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 325"><rect width="800" height="325" fill="white"/><text x="20" y="25">'+escape(title)+'</text>'+''.join(views)+'</svg>',encoding='utf-8')
        files.append({'path':str(path),'evidence':item})
    return {'ok':True,'views':files,'analysis_complete':report['analysis_complete'],
            'scope':'up to 25 geometric triangle closeups in three projections; not native captures or full garment context'}
