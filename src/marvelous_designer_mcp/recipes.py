"""Portable, data-only garment recipes. Also sent to MD; standard library only."""
import copy
import math


def recipe_number(value, label, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(label + ' must be finite')
    if abs(value) > 10_000_000 or (positive and value <= 0):
        raise ValueError(label + ' is outside the supported range')
    return float(value)


def recipe_keys(value, allowed, required, label):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ValueError('Invalid fields in ' + label)


def recipe_polygon(points, coordinate_scale=1.0):
    scale = recipe_number(coordinate_scale, 'coordinate_scale', True)
    if not isinstance(points, list) or not 3 <= len(points) <= 1024:
        raise ValueError('A polygon needs 3–1024 vertices; omit the repeated closing point')
    polygon = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError('Each polygon point must be [x, y]')
        polygon.append(tuple(recipe_number(recipe_number(v, 'coordinate') * scale, 'scaled coordinate') for v in point))
    if len(set(polygon)) != len(polygon):
        raise ValueError('Polygon vertices must be unique')

    def cross(a, b, c):
        return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])

    def on_segment(a, b, c):
        return (abs(cross(a, b, c)) < 1e-8 and min(a[0], b[0]) <= c[0] <= max(a[0], b[0])
                and min(a[1], b[1]) <= c[1] <= max(a[1], b[1]))

    n = len(polygon)
    for i in range(n):
        a, b = polygon[i], polygon[(i+1) % n]
        for j in range(i+1, n):
            if j == i+1 or (i == 0 and j == n-1):
                continue
            c, d = polygon[j], polygon[(j+1) % n]
            if (cross(a, b, c)*cross(a, b, d) < 0 and cross(c, d, a)*cross(c, d, b) < 0
                    or any((on_segment(a, b, c), on_segment(a, b, d), on_segment(c, d, a), on_segment(c, d, b)))):
                raise ValueError('Polygon self-intersects or touches a nonadjacent edge')
    area = abs(sum(polygon[i][0]*polygon[(i+1) % n][1] - polygon[(i+1) % n][0]*polygon[i][1] for i in range(n))) / 2
    if area < 1e-6:
        raise ValueError('Polygon has zero area')
    return polygon


def validate_recipe(recipe):
    recipe_keys(recipe, ('schema_version', 'name', 'pieces', 'seams', 'fabric', 'export', 'preview_count', 'measurements'),
                ('schema_version', 'name', 'pieces'), 'recipe')
    if type(recipe['schema_version']) is not int or recipe['schema_version'] != 1:
        raise ValueError('Unsupported recipe schema_version')
    if not isinstance(recipe['name'], str) or not recipe['name'].strip() or '\x00' in recipe['name']:
        raise ValueError('Recipe name is required')
    pieces = recipe['pieces']
    if not isinstance(pieces, list) or not 1 <= len(pieces) <= 100:
        raise ValueError('Recipe needs 1–100 pieces')
    normalized = copy.deepcopy(recipe)
    ids = {}
    for piece in normalized['pieces']:
        recipe_keys(piece, ('id', 'name', 'points'), ('id', 'name', 'points'), 'piece')
        for key in ('id', 'name'):
            if not isinstance(piece[key], str) or not piece[key].strip() or '\x00' in piece[key]:
                raise ValueError('Piece id/name must be nonempty')
        if piece['id'] in ids:
            raise ValueError('Duplicate recipe piece id')
        piece['points'] = [list(p) for p in recipe_polygon(piece['points'])]
        ids[piece['id']] = piece
    seams = normalized.setdefault('seams', [])
    if not isinstance(seams, list) or len(seams) > 500:
        raise ValueError('Recipe seams must be a list of at most 500 pairs')
    used = set()
    for seam in seams:
        recipe_keys(seam, ('piece_a', 'line_a', 'piece_b', 'line_b', 'direction_a', 'direction_b'),
                    ('piece_a', 'line_a', 'piece_b', 'line_b', 'direction_a', 'direction_b'), 'seam')
        endpoints = []
        for side in ('a', 'b'):
            pid, edge = seam['piece_'+side], seam['line_'+side]
            if not isinstance(pid, str) or pid not in ids or type(edge) is not int or not 0 <= edge < len(ids[pid]['points']):
                raise ValueError('Invalid recipe sewing endpoint')
            if type(seam['direction_'+side]) is not bool:
                raise ValueError('Sewing directions must be explicit booleans')
            endpoints.append((pid, edge))
        if endpoints[0] == endpoints[1] or any(e in used for e in endpoints):
            raise ValueError('An edge cannot be sewn to itself or reused in this recipe')
        used.update(endpoints)
    if 'fabric' in normalized:
        fabric = normalized['fabric']
        recipe_keys(fabric, ('path', 'assignment_mode'), ('path',), 'fabric')
        if not isinstance(fabric['path'], str) or not fabric['path']:
            raise ValueError('Fabric path is required')
        mode = fabric.setdefault('assignment_mode', 1)
        if type(mode) is not int or mode not in (1, 2, 3):
            raise ValueError('Fabric assignment_mode must be 1, 2 or 3')
    export = normalized.setdefault('export', {})
    recipe_keys(export, ('scale', 'thin', 'single_object', 'include_avatar', 'unified_uv'), (), 'export')
    if 'scale' in export:
        recipe_number(export['scale'], 'export scale', True)
    for key in set(export) - {'scale'}:
        if type(export[key]) is not bool:
            raise ValueError('Export flags must be booleans')
    count = normalized.setdefault('preview_count', 4)
    if type(count) is not int or not 0 <= count <= 8:
        raise ValueError('Recipe preview_count must be 0–8')
    if 'measurements' in normalized:
        if not isinstance(normalized['measurements'], dict):
            raise ValueError('Measurements must be an object')
        for value in normalized['measurements'].values():
            recipe_number(value, 'measurement')
    return normalized


def skirt_recipe(waist_cm, length_cm, hem_cm, ease_cm=2.0, native_units_per_cm=10.0, vertical_direction='down'):
    waist = recipe_number(waist_cm, 'waist_cm', True)
    length = recipe_number(length_cm, 'length_cm', True)
    hem = recipe_number(hem_cm, 'hem_cm', True)
    ease = recipe_number(ease_cm, 'ease_cm')
    if ease < 0 or hem < waist + ease:
        raise ValueError('Ease must be nonnegative; hem must be at least waist plus ease')
    scale = recipe_number(native_units_per_cm, 'native_units_per_cm', True)
    top, bottom, height = (waist+ease)/2*scale, hem/2*scale, length*scale
    if vertical_direction not in ('down','up'):
        raise ValueError('vertical_direction must be down (negative Y) or up (positive Y)')
    height *= -1 if vertical_direction=='down' else 1
    # Boundary: waist, right side, hem, left side. MD 2026 live trial used negative Y downward.
    points = [[-top/2, 0], [top/2, 0], [bottom/2, height], [-bottom/2, height]]
    back = [[x + bottom + 100, y] for x, y in points]
    return validate_recipe({'schema_version': 1, 'name': 'Two-panel skirt block',
        'measurements': {'waist_cm': waist, 'length_cm': length, 'hem_cm': hem, 'ease_cm': ease,
                         'native_units_per_cm': scale},
        'pieces': [{'id': 'front', 'name': 'Skirt Front', 'points': points},
                   {'id': 'back', 'name': 'Skirt Back', 'points': back}],
        'seams': [{'piece_a': 'front', 'line_a': 1, 'piece_b': 'back', 'line_b': 3, 'direction_a': True, 'direction_b': False},
                  {'piece_a': 'front', 'line_a': 3, 'piece_b': 'back', 'line_b': 1, 'direction_a': True, 'direction_b': False}]})


def garment_block(kind, measurements, native_units_per_cm=10.0, vertical_direction='down'):
    """Local draft geometry only; inspect native edge maps before constructing seams."""
    scale=recipe_number(native_units_per_cm,'native_units_per_cm',True)
    if vertical_direction not in ('down','up'):
        raise ValueError('vertical_direction must be down or up')
    sign=-1 if vertical_direction=='down' else 1
    values={key:recipe_number(value,key,key!='ease_cm')*scale for key,value in measurements.items()}
    if values['ease_cm']<0:
        raise ValueError('ease_cm must be nonnegative')
    def vertices(points):
        return [[x,sign*y,kind] for x,y,kind in points]
    if kind=='bodice':
        width=values['bust_cm']/4+values['ease_cm']/4
        length,shoulder,neck,arm=values['length_cm'],values['shoulder_width_cm']/2,values['neck_width_cm']/2,values['armhole_depth_cm']
        if not neck<shoulder<width or arm>=length or values['front_neck_depth_cm']>=arm or values['back_neck_depth_cm']>=arm:
            raise ValueError('Require neck < shoulder < panel width, and neck depth < armhole depth < length')
        pieces=[]
        shoulder_drop=min(scale*2,arm/4)
        for side in ('front','back'):
            depth=values[side+'_neck_depth_cm']
            points=[(-neck,0,0),(0,depth,2),(neck,0,0),(shoulder,shoulder_drop,0),
                    (shoulder,arm/2,2),(width,arm,0),(width,length,0),
                    (-width,length,0),(-width,arm,0),(-shoulder,arm/2,2),(-shoulder,shoulder_drop,0)]
            pieces.append({'id':side,'name':'Bodice '+side.title(),'vertices':vertices(points)})
    elif kind=='sleeve':
        width=values['bicep_cm']+values['ease_cm']
        cuff=values['cuff_cm']
        length,cap=values['length_cm'],values['cap_height_cm']
        if cap>=length or cuff>width:
            raise ValueError('Cap height must be less than length; cuff must not exceed eased bicep')
        points=[(-width/2,cap,0),(-width/3,cap/3,2),(0,0,0),
                (width/3,cap/3,2),(width/2,cap,0),(cuff/2,length,0),(-cuff/2,length,0)]
        pieces=[{'id':'sleeve','name':'Sleeve Block','vertices':vertices(points)}]
    else:
        raise ValueError('Unknown garment block')
    for piece in pieces:
        recipe_polygon([point[:2] for point in piece['vertices']])
    return {'ok':True,'block':{'kind':kind,'measurements_cm':measurements,'native_units_per_cm':scale,
            'vertical_direction':vertical_direction,'downward_y_sign':sign,'pieces':pieces},
            'workflow':['create_curved_pattern for each piece','inspect_native_pattern_geometry',
                        'bind actual edge indices','diagnose seam lengths','checkpoint then sew',
                        'discover avatar arrangement points','arrange_patterns_by_name','run_fitting_pass'],
            'fit_certified':False,'sleeve_cap_matched':False,
            'scope':'draft templates, not a fitted sloper or garment recipe; no inferred sewing indices, darts, closures or seam allowance'}
