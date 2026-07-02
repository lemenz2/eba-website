from flask import Flask, render_template, request, jsonify
import os
import ezdxf
import math
import json
from collections import defaultdict
from werkzeug.utils import secure_filename
import uuid
import traceback

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

ALLOWED_EXTENSIONS = {'dxf'}
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

FIXED_MARGIN_MM = 10

def load_materials():
    try:
        with open('materials.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get('materials', [])
    except:
        return [
            {"id": "steel_black", "name": "Acier Noir", "price_per_kg": 3.50, "density_kg_per_m3": 7850},
            {"id": "aluminum", "name": "Aluminium", "price_per_kg": 12.50, "density_kg_per_m3": 2700},
        ]

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def polygon_area(vertices):
    if len(vertices) < 3:
        return 0
    area = 0
    n = len(vertices)
    for i in range(n):
        x1, y1 = vertices[i]
        x2, y2 = vertices[(i+1) % n]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0

def polygon_perimeter(vertices):
    if len(vertices) < 2:
        return 0
    perimeter = 0
    n = len(vertices)
    for i in range(n):
        x1, y1 = vertices[i]
        x2, y2 = vertices[(i+1) % n]
        perimeter += math.sqrt((x2-x1)**2 + (y2-y1)**2)
    return perimeter

def sample_arc_points(arc, num_points=20):
    center = (arc.dxf.center.x, arc.dxf.center.y)
    radius = arc.dxf.radius
    start_angle = math.radians(arc.dxf.start_angle)
    end_angle = math.radians(arc.dxf.end_angle)
    if end_angle < start_angle:
        end_angle += 2 * math.pi
    points = []
    for i in range(num_points+1):
        angle = start_angle + (end_angle - start_angle) * i / num_points
        x = center[0] + radius * math.cos(angle)
        y = center[1] + radius * math.sin(angle)
        points.append((round(x, 2), round(y, 2)))
    return points

def sample_spline_points(spline, num_points=30):
    try:
        points = list(spline.flattening(2.0))
        sampled = []
        step = max(1, len(points) // num_points)
        for i in range(0, len(points), step):
            sampled.append((round(points[i].x, 2), round(points[i].y, 2)))
        if points[-1] not in sampled:
            sampled.append((round(points[-1].x, 2), round(points[-1].y, 2)))
        return sampled
    except:
        return []

def find_all_closed_loops(msp):
    graph = defaultdict(list)
    
    for line in msp.query('LINE'):
        s = (round(line.dxf.start.x, 2), round(line.dxf.start.y, 2))
        e = (round(line.dxf.end.x, 2), round(line.dxf.end.y, 2))
        graph[s].append(e)
        graph[e].append(s)
    
    for arc in msp.query('ARC'):
        pts = sample_arc_points(arc, 15)
        for i in range(len(pts)-1):
            p1, p2 = pts[i], pts[i+1]
            graph[p1].append(p2)
            graph[p2].append(p1)
    
    for spline in msp.query('SPLINE'):
        pts = sample_spline_points(spline, 20)
        for i in range(len(pts)-1):
            p1, p2 = pts[i], pts[i+1]
            graph[p1].append(p2)
            graph[p2].append(p1)
    
    for circle in msp.query('CIRCLE'):
        cx, cy = circle.dxf.center.x, circle.dxf.center.y
        r = circle.dxf.radius
        pts = []
        for i in range(36):
            ang = 2 * math.pi * i / 36
            x = cx + r * math.cos(ang)
            y = cy + r * math.sin(ang)
            pts.append((round(x, 2), round(y, 2)))
        for i in range(len(pts)-1):
            p1, p2 = pts[i], pts[i+1]
            graph[p1].append(p2)
            graph[p2].append(p1)
        graph[pts[-1]].append(pts[0])
        graph[pts[0]].append(pts[-1])
    
    if not graph:
        return []
    
    cycles = []
    visited_edges = set()
    
    def find_cycle(start, current, path, edges_used):
        if len(path) > 2 and current == start:
            return path
        for nb in graph.get(current, []):
            edge = tuple(sorted([current, nb]))
            if edge not in edges_used:
                edges_used.add(edge)
                res = find_cycle(start, nb, path + [nb], edges_used)
                if res:
                    return res
                edges_used.remove(edge)
        return None
    
    processed = set()
    for point in list(graph.keys()):
        if point in processed:
            continue
        if len(graph[point]) >= 2:
            edges_used = set()
            cycle = find_cycle(point, point, [point], edges_used)
            if cycle and len(cycle) >= 3:
                unique = []
                for p in cycle:
                    if p not in unique:
                        unique.append(p)
                if len(unique) >= 3:
                    for p in unique:
                        processed.add(p)
                    cycles.append(unique)
    
    return cycles

def calculate_line_length(line):
    start = (line.dxf.start.x, line.dxf.start.y)
    end = (line.dxf.end.x, line.dxf.end.y)
    return math.sqrt((end[0]-start[0])**2 + (end[1]-start[1])**2)

def calculate_arc_length(arc):
    radius = arc.dxf.radius
    start_angle = math.radians(arc.dxf.start_angle)
    end_angle = math.radians(arc.dxf.end_angle)
    return radius * abs(end_angle - start_angle)

def calculate_spline_length(spline):
    try:
        points = list(spline.flattening(1.0))
        length = 0
        for i in range(len(points)-1):
            dx = points[i+1].x - points[i].x
            dy = points[i+1].y - points[i].y
            length += math.sqrt(dx*dx + dy*dy)
        return length
    except:
        return 0

@app.route('/')
def index():
    return render_template('index.html', materials=load_materials())

@app.route('/upload', methods=['POST'])
def upload_file():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file'}), 400
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        if not allowed_file(file.filename):
            return jsonify({'error': 'Only DXF files allowed'}), 400
        
        fname = secure_filename(file.filename)
        uniq = f"{uuid.uuid4().hex}_{fname}"
        path = os.path.join(app.config['UPLOAD_FOLDER'], uniq)
        file.save(path)
        
        try:
            doc = ezdxf.readfile(path)
            msp = doc.modelspace()
            
            # Find all closed loops
            loops = find_all_closed_loops(msp)
            
            if not loops:
                os.remove(path)
                return jsonify({'error': 'No closed shapes found'}), 400
            
            # Get loop data with area and perimeter
            loops_data = []
            for loop in loops:
                area = polygon_area(loop)
                perimeter = polygon_perimeter(loop)
                loops_data.append({
                    'vertices': loop,
                    'area': area,
                    'perimeter': perimeter,
                    'center': (sum(p[0] for p in loop)/len(loop), sum(p[1] for p in loop)/len(loop))
                })
            
            # Sort by area (largest first)
            loops_data.sort(key=lambda x: x['area'], reverse=True)
            
            # Filter out tiny shapes (noise) - less than 1% of largest area
            largest_area = loops_data[0]['area'] if loops_data else 0
            min_area_threshold = max(10, largest_area * 0.001)  # At least 10mm² or 0.1% of largest
            
            # Keep shapes above threshold
            filtered_loops = [l for l in loops_data if l['area'] >= min_area_threshold]
            
            if not filtered_loops:
                filtered_loops = loops_data[:5]  # Keep at least top 5 if filtering is too aggressive
            
            # Store for session
            session_id = uniq
            app.config['sessions'] = app.config.get('sessions', {})
            app.config['sessions'][session_id] = {
                'path': path,
                'loops_data': filtered_loops,
                'msp': msp
            }
            
            # Calculate total cut length (auto-detect)
            outer = filtered_loops[0] if filtered_loops else None
            holes = filtered_loops[1:] if len(filtered_loops) > 1 else []
            
            total_cut = 0
            if outer:
                total_cut += outer['perimeter']
            for h in holes:
                total_cut += h['perimeter']
            
            for line in msp.query('LINE'):
                l = calculate_line_length(line)
                if l > 0.5:
                    total_cut += l
            for arc in msp.query('ARC'):
                total_cut += calculate_arc_length(arc)
            for spline in msp.query('SPLINE'):
                total_cut += calculate_spline_length(spline)
            
            # Get bounding box
            all_points = []
            for line in msp.query('LINE'):
                all_points.append((line.dxf.start.x, line.dxf.start.y))
                all_points.append((line.dxf.end.x, line.dxf.end.y))
            for circle in msp.query('CIRCLE'):
                cx, cy = circle.dxf.center.x, circle.dxf.center.y
                r = circle.dxf.radius
                all_points.append((cx + r, cy + r))
                all_points.append((cx - r, cy - r))
            for spline in msp.query('SPLINE'):
                for p in spline.flattening(5.0):
                    all_points.append((p.x, p.y))
            for arc in msp.query('ARC'):
                cx, cy = arc.dxf.center.x, arc.dxf.center.y
                r = arc.dxf.radius
                all_points.append((cx + r, cy + r))
                all_points.append((cx - r, cy - r))
            
            if all_points:
                min_x = min(p[0] for p in all_points)
                max_x = max(p[0] for p in all_points)
                min_y = min(p[1] for p in all_points)
                max_y = max(p[1] for p in all_points)
                part_w = max_x - min_x
                part_h = max_y - min_y
                sheet_w = part_w + (FIXED_MARGIN_MM * 2)
                sheet_h = part_h + (FIXED_MARGIN_MM * 2)
            else:
                part_w = part_h = sheet_w = sheet_h = 0
            
            # Prepare shape data for frontend
            shapes = []
            for i, loop in enumerate(filtered_loops):
                # Simplify vertices for large shapes (reduce data size)
                verts = loop['vertices']
                if len(verts) > 200:
                    # Reduce points for large shapes
                    step = max(1, len(verts) // 150)
                    verts = verts[::step]
                    # Make sure we have at least 3 points and close properly
                    if len(verts) < 3:
                        verts = loop['vertices'][:50]  # fallback
                
                shapes.append({
                    'id': i,
                    'vertices': verts,
                    'area': round(loop['area'], 2),
                    'perimeter': round(loop['perimeter'], 2),
                    'center': loop['center']
                })
            
            return jsonify({
                'success': True,
                'session_id': session_id,
                'shapes': shapes,
                'part_width': round(part_w, 2),
                'part_height': round(part_h, 2),
                'sheet_width': round(sheet_w, 2),
                'sheet_height': round(sheet_h, 2),
                'total_cut_length': round(total_cut, 2),
                'outer_id': 0,
                'hole_ids': [i for i in range(1, len(shapes))],
                'margin': FIXED_MARGIN_MM,
                'all_loops_count': len(loops_data),
                'filtered_count': len(filtered_loops)
            })
            
        except Exception as e:
            if os.path.exists(path):
                os.remove(path)
            return jsonify({'error': str(e)}), 400
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/calculate', methods=['POST'])
def calculate():
    try:
        data = request.get_json()
        session_id = data.get('session_id')
        outer_id = data.get('outer_id', 0)
        hole_ids = data.get('hole_ids', [])
        material_id = data.get('material', 'steel_black')
        thickness = float(data.get('thickness', 1.0))
        cut_speed = float(data.get('cutting_speed', 4000))
        pierce_time = float(data.get('piercing_time', 5))
        hourly = float(data.get('hourly_rate', 250))
        parts = float(data.get('parts_per_sheet', 1))
        
        session = app.config.get('sessions', {}).get(session_id)
        if not session:
            return jsonify({'error': 'Session expired'}), 400
        
        msp = session['msp']
        loops_data = session['loops_data']
        
        outer = loops_data[outer_id] if outer_id < len(loops_data) else None
        holes = [loops_data[i] for i in hole_ids if i < len(loops_data)]
        
        if not outer:
            return jsonify({'error': 'No outer shape selected'}), 400
        
        # Calculate total cut length
        total_cut = outer['perimeter']
        for h in holes:
            total_cut += h['perimeter']
        
        for line in msp.query('LINE'):
            l = calculate_line_length(line)
            if l > 0.5:
                total_cut += l
        for arc in msp.query('ARC'):
            total_cut += calculate_arc_length(arc)
        for spline in msp.query('SPLINE'):
            total_cut += calculate_spline_length(spline)
        
        piercings = len(holes)
        cut_time = (total_cut * 60 / cut_speed) + (piercings * pierce_time)
        
        # Bounding box with margin
        all_points = []
        for line in msp.query('LINE'):
            all_points.append((line.dxf.start.x, line.dxf.start.y))
            all_points.append((line.dxf.end.x, line.dxf.end.y))
        for circle in msp.query('CIRCLE'):
            cx, cy = circle.dxf.center.x, circle.dxf.center.y
            r = circle.dxf.radius
            all_points.append((cx + r, cy + r))
            all_points.append((cx - r, cy - r))
        for spline in msp.query('SPLINE'):
            for p in spline.flattening(5.0):
                all_points.append((p.x, p.y))
        for arc in msp.query('ARC'):
            cx, cy = arc.dxf.center.x, arc.dxf.center.y
            r = arc.dxf.radius
            all_points.append((cx + r, cy + r))
            all_points.append((cx - r, cy - r))
        
        if all_points:
            min_x = min(p[0] for p in all_points) - FIXED_MARGIN_MM
            max_x = max(p[0] for p in all_points) + FIXED_MARGIN_MM
            min_y = min(p[1] for p in all_points) - FIXED_MARGIN_MM
            max_y = max(p[1] for p in all_points) + FIXED_MARGIN_MM
            sheet_w = max_x - min_x
            sheet_h = max_y - min_y
            sheet_area = sheet_w * sheet_h
        else:
            sheet_w = sheet_h = sheet_area = 0
        
        # Get material
        mats = load_materials()
        material = next((m for m in mats if m['id'] == material_id), mats[0])
        
        weight = (sheet_area * thickness * material['density_kg_per_m3']) / 1000000000
        mat_price = weight * material['price_per_kg']
        labor = (cut_time + (60 / parts)) * hourly / 3600
        final_price = mat_price + labor
        
        # Hole data for table
        holes_data = []
        for i, h in enumerate(holes, 1):
            d = 2 * math.sqrt(h['area'] / math.pi) if h['area'] > 0 else 0
            holes_data.append({
                'number': i,
                'type': 'HOLE',
                'area': round(h['area'], 2),
                'diameter': round(d, 2),
                'perimeter': round(h['perimeter'], 2)
            })
        
        return jsonify({
            'success': True,
            'part_width': round(sheet_w - (FIXED_MARGIN_MM * 2), 2),
            'part_height': round(sheet_h - (FIXED_MARGIN_MM * 2), 2),
            'sheet_width': round(sheet_w, 2),
            'sheet_height': round(sheet_h, 2),
            'sheet_area': round(sheet_area, 2),
            'outer_perimeter': round(outer['perimeter'], 2),
            'hole_count': len(holes_data),
            'holes': holes_data,
            'total_cut_length': round(total_cut, 2),
            'number_of_piercings': piercings,
            'weight_kg': round(weight, 6),
            'material_price': round(mat_price, 3),
            'material_name': material['name'],
            'cutting_time_minutes': round(cut_time, 2),
            'cutting_price': round(labor, 3),
            'final_price': round(final_price, 3),
            'margin': FIXED_MARGIN_MM
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print("="*60)
    print("DXF LASER PRICING - INTERACTIVE VIEWPORT")
    print("="*60)
    
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
