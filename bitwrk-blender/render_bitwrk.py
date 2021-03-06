#  BitWrk - A Bitcoin-friendly, anonymous marketplace for computing power
#  Copyright (C) 2013-2014  Jonas Eschenburg <jonas@bitwrk.net>
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <http:#www.gnu.org/licenses/>.
import subprocess

bl_info = {
    "name": "BitWrk Distributed Rendering",
    "description": "Support for distributed rendering using BitWrk, a marketplace for computing power",
    "author": "Jonas Eschenburg",
    "version": (0, 3, 0),
    "blender": (2, 69, 0),
    "category": "Render",
}

# Minimum Python version: 3.2 (tempfile.TemporaryDirectory)

import bpy, os, sys, http.client, select, struct, tempfile, urllib.request, colorsys, math
import webbrowser, time, traceback
import hashlib
from bpy.props import StringProperty, IntProperty, PointerProperty, EnumProperty

def get_article_id(complexity):
    major, minor, micro = bpy.app.version
    return "net.bitwrk/blender/0/{}.{}/{}".format(major, minor, complexity)
    
# used by BitWrkSettings PropertyGroup
def set_complexity(self, value):
    self['complexity'] = value
    
def get_max_cost(settings):
    if settings.complexity == '512M':
        return  512*1024*1024
    elif settings.complexity == '2G':
        return  2*1024*1024*1024
    elif settings.complexity == '8G':
        return  8*1024*1024*1024
    elif settings.complexity == '32G':
        return  32*1024*1024*1024
    else:
        print(dir(settings), settings)
        print(settings.complexity)
        raise RuntimeError()

# Functions for probing host:port settings for a running BitWrk client
LAST_PROBE_TIME = time.time()
LAST_PROBE_RESULT = False
LAST_PROBE_SETTINGS = None
def probe_bitwrk_client(settings):
    global LAST_PROBE_TIME, LAST_PROBE_RESULT, LAST_PROBE_SETTINGS
    if settings_string(settings) == LAST_PROBE_SETTINGS and time.time() - LAST_PROBE_TIME < 5.0:
        return LAST_PROBE_RESULT
        
    LAST_PROBE_RESULT=do_probe_bitwrk_client(settings)
    LAST_PROBE_TIME=time.time()
    LAST_PROBE_SETTINGS=settings_string(settings)
    return LAST_PROBE_RESULT
    
def settings_string(settings):
    return "{}:{}".format(settings.bitwrk_client_host, settings.bitwrk_client_port)
    
def do_probe_bitwrk_client(settings):
    conn = http.client.HTTPConnection(
        host=settings.bitwrk_client_host, port=settings.bitwrk_client_port,
        timeout=1)
    try:
        conn.request('GET', "/id")
        resp = conn.getresponse()
        if resp.status != http.client.OK:
            return False
        data = resp.read(256)
        if data != b"BitWrk Go Client":
            return False
        conn.request('GET', "/version")
        resp = conn.getresponse()
        if resp.status != http.client.OK:
            return False
        data = resp.read(256)
        if not data.startswith(b"0.3."):
            return False
        return True
    except:
        try:
            conn.close()
        except:
            pass
        return False
            
    
class BitWrkSettings(bpy.types.PropertyGroup):
    
    @classmethod
    def register(settings):
        settings.bitwrk_client_host = StringProperty(
            name="BitWrk client host",
            description="IP or name of host running local BitWrk client",
            maxlen=180,
            default="localhost")
        settings.bitwrk_client_port = IntProperty(
            name="BitWrk client port",
            description="TCP port the local BitWrk client listens on",
            default=8081,
            min=1,
            max=65535)
        settings.complexity = EnumProperty(
            name="Complexity",
            description="Defines the maximum allowed computation complexity for each rendered tile",
            items=[
                ('512M', "0.5 Giga-rays", "", 3),
                ('2G',  " 2 Giga-rays", "", 0),
                ('8G',  " 8 Giga-rays", "", 1),
                ('32G', "32 Giga-rays", "", 2)],
            default='8G',
            set=set_complexity,
            get=lambda value: value['complexity'])
        settings.concurrency = IntProperty(
            name="Concurrent tiles",
            description="Maximum number of BitWrk trades active in parallel",
            default=4,
            min=1,
            max=256)
        
        bpy.types.Scene.bitwrk_settings = PointerProperty(type=BitWrkSettings, name="BitWrk Settings", description="Settings for using the BitWrk service")

    @classmethod
    def unregister(cls):
        del bpy.types.Scene.bitwrk_settings

class StartBrowserOperator(bpy.types.Operator):
    """Open BitWrk admin console in web browser"""
    bl_idname = "bitwrk.startbrowser"
    bl_label = "Open BitWrk Client User Interface"

    @classmethod
    def poll(cls, context):
        return probe_bitwrk_client(context.scene.bitwrk_settings)
    
    def execute(self, context):
        settings=context.scene.bitwrk_settings
        webbrowser.open("http://{}:{}/".format(settings.bitwrk_client_host, settings.bitwrk_client_port)) 
        return {'FINISHED'}

    def invoke(self, context, event):
        return self.execute(context)


class RENDER_PT_bitwrk_settings(bpy.types.Panel):
    bl_label = "BitWrk Settings Panel"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "render"
    COMPAT_ENGINES = {"BITWRK_RENDER"}
    
    @classmethod
    def poll(cls, context):
        rd = context.scene.render
        return rd.engine == 'BITWRK_RENDER' and not rd.use_game_engine
    
    def draw(self, context):
        settings=context.scene.bitwrk_settings
        self.layout.label("Local BitWrk client host and port:")
        row = self.layout.row()
        row.prop(settings, "bitwrk_client_host", text="")
        row.prop(settings, "bitwrk_client_port", text="")
        if probe_bitwrk_client(settings):
            self.layout.operator("bitwrk.startbrowser", icon='URL')
        else:
            self.layout.label("No BitWrk client at this address", icon='ERROR')
        
        self.layout.prop(settings, "complexity")
        row = self.layout.split(0.333)
        row.label("Article id: ", icon="RNDCURVE")
        row.label(get_article_id(settings.complexity))
        
        self.layout.prop(settings, "concurrency")

class Chunked:
    """Wraps individual write()s into http chunked encoding."""
    def __init__(self, conn):
        self.conn = conn
    
    def write(self, data):
        if type(data) != bytes:
            data = data.encode('utf-8')
        if len(data) == 0:
            return
        self.conn.send(("%x" % len(data)).encode('ascii'))
        self.conn.send(b'\r\n')
        self.conn.send(data)
        self.conn.send(b'\r\n')
        
    def close(self):
        self.conn.send(b'0\r\n\r\n') # An empty chunk terminates the transmission
        
class Tagged:
    """Produces an IFF-like stream"""
    def __init__(self, out):
        self.out = out
        self.aliases = {}
        
    def writeResource(self, file, origpath, abspath):
        """Writes a resource linked by the blend file into the stream.
        Chunk format is:
         'rsrc' CHUNKLENGTH
                ALIASLENGTH alias...
                ORIGLENGTH origpath...
                FILELENGTH filedata...
        """
         
        if type(origpath) != bytes:
            origpath = origpath.encode('utf-8')
         
        if origpath in self.aliases:
            # Only write resources not written yet
            return
        
        alias = resource_id(abspath).encode('utf-8')
        
        file.seek(0, os.SEEK_END)
        filelength = file.tell()
        file.seek(0, os.SEEK_SET)

        # chunk size must not exceed MAX_INT
        chunklength = filelength + len(origpath) + len(alias) + 12
        if chunklength > 0x8fffffff:
            raise RuntimeError('File is too big to be written: %d bytes' % length)
        
        self.aliases[origpath] = alias
        self.out.write(struct.pack('>4sI', b'rsrc', chunklength))
        self.out.write(struct.pack('>I', len(alias)))
        self.out.write(alias)
        self.out.write(struct.pack('>I', len(origpath)))
        self.out.write(origpath)
        self.out.write(struct.pack('>I', filelength))
        while filelength > 0:
            data = file.read(min(filelength, 4096))
            filelength = filelength - len(data)
            self.out.write(data)
        return alias
        
    def writeFile(self, tag, f):
        """Writes the contents of a file into the stream"""
        if type(tag) != bytes:
            tag = tag.encode('utf-8')
        if len(tag) != 4:
            raise RuntimeError('Tag must be 4 byte long (was: %x)' % tag)
        f.seek(0, os.SEEK_END)
        length = f.tell()
        if length > 0x8fffffff:
            raise RuntimeError('File is too big to be written: %d bytes' % length)
        f.seek(0, os.SEEK_SET)
        
        self.out.write(struct.pack('>4sI', tag, length))
        while length > 0:
            data = f.read(min(length, 4096))
            length = length - len(data)
            self.out.write(data)
            
    def writeData(self, tag, value):
        if type(tag) != bytes:
            tag = tag.encode('utf-8')
        if type(value) != bytes:
            value = value.encode('utf-8')
        if len(tag) != 4:
            raise RuntimeError('Tag must be 4 byte long (was: %x)' % tag)
        if len(value) > 0x8fffffff:
            raise RuntimeError('Values too long: len(value)=%d' % len(value))
        self.out.write(struct.pack('>4sI', tag, len(value)))
        self.out.write(value)
        
    def writeInt(self, tag, value):
        self.writeData(tag, struct.pack(">i", value))
        
class Tile:
    def __init__(self, frame, minx, miny, resx, resy, color):
        self.conn = None
        self.result = None
        self.frame = frame
        self.minx = minx
        self.miny = miny
        self.resx = resx
        self.resy = resy
        self.color = color
        self.success = False
        
    def dispatch(self, settings, data, filepath, engine):
        """Dispatches one tile to the bitwrk client.
        The complete blender data is packed into the transmission.
        """ 
        # draw rect in preview color
        tile = engine.begin_result(self.minx, self.miny, self.resx, self.resy)
        tile.layers[0].rect = [self.color] * (self.resx*self.resy)
        engine.end_result(tile)
        
        self.result = engine.begin_result(self.minx, self.miny, self.resx, self.resy)
        self.conn = http.client.HTTPConnection(
            settings.bitwrk_client_host, settings.bitwrk_client_port,
            timeout=600)
        try:
            self.conn.putrequest("POST", "/buy/" + get_article_id(settings.complexity))
            self.conn.putheader('Transfer-Encoding', 'chunked')
            self.conn.endheaders()
            chunked = Chunked(self.conn)
            try:
                tagged = Tagged(chunked)
                tagged.writeInt('xmin', self.minx)
                tagged.writeInt('ymin', self.miny)
                tagged.writeInt('xmax', self.minx+self.resx-1)
                tagged.writeInt('ymax', self.miny+self.resy-1)
                tagged.writeInt('fram', self.frame)
                for path, abspath in zip(bpy.utils.blend_paths(absolute=False, packed=True),
                                         bpy.utils.blend_paths(absolute=True, packed=True)):
                    try:
                        with open(abspath, "rb") as file:
                            alias = tagged.writeResource(file, path, abspath)
                            engine.report({'INFO'}, "Successfully bundled resource {} = {}".format(alias, path))
                    except FileNotFoundError:
                        engine.report({'WARNING'}, "Error bundling resource {}".format(path))
                    except IsADirectoryError:
                        pass
                with open(filepath, "rb") as file:
                    tagged.writeFile('blen', file)
            finally:
                chunked.close()
        except:
            print("Exception in dispatch:", sys.exc_info())
            engine.report({'ERROR'}, "Exception in dispatch: {}".format(traceback.format_exc()))
            self.conn.close()
            self.conn = None
            self.result.layers[0].rect = [[1,0,0,1]] * (self.resx*self.resy)
            engine.end_result(self.result)
            self.result = None
            return False
        else:
            return True
        
    def collect(self, settings, engine, is_multilayer):
        if self.conn is None:
            return
        try:
            resp = self.conn.getresponse()
            try:
                if resp.status == 303:
                    print("Fetching result from", resp.getheader("Location"))
                    location = resp.getheader("Location")
                    with tempfile.TemporaryDirectory() as tmpdir:
                        filename = os.path.join(tmpdir, "result.exr")
                        with open(filename, "wb") as tmpfile,\
                            urllib.request.urlopen("http://{}:{}{}".format(
                                settings.bitwrk_client_host,
                                settings.bitwrk_client_port,
                                location)) as response:
                            data = response.read(32768)
                            while len(data) > 0:
                                tmpfile.write(data)
                                data = response.read(32768)
                        if is_multilayer:
                            self.result.load_from_file(filename)
                        else:
                            self.result.layers[0].load_from_file(filename)
                        self.success = True
                else:
                    message = resp.read(1024).decode('ascii')
                    raise RuntimeError("Response status is {}, message was: {}".format(resp.status, message))
            finally:
                resp.close()
            engine.end_result(self.result)
        except:
            print("Exception in collect:", sys.exc_info())
            engine.report({'WARNING'}, "Exception in collect: {}".format(traceback.format_exc()))
            self.result.layers[0].rect = [[1,0,0,1]] * (self.resx*self.resy)
            engine.end_result(self.result)
            self.result = None
        finally:
            self.conn.close()
            self.conn = None
            
    def fileno(self):
        return self.conn.sock.fileno()
        
    def cancel(self):
        try:
            if self.conn is not None:
                self.conn.close()
        except:
            pass
        finally:
            self.conn = None


class BitWrkRenderEngine(bpy.types.RenderEngine):
    """BitWrk Rendering Engine"""
    bl_idname = "BITWRK_RENDER"
    bl_label = "BitWrk distributed rendering"
    bl_description = "Performs distributed rendering using the BitWrk marketplace for compute power"
    
    def render(self, scene):
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                self._doRender(scene, tmpdir)
        except:
            self.report({'ERROR'}, "Exception while rendering: {}".format(traceback.format_exc()))
            
        
    def _doRender(self, scene, tmpdir):
        # Export the file to a temporary directory and call this script
        # in a separate blender session for remapping all paths. This
        # seems to be the only way to change paths on the temp file only,
        # without affecting the original file.
        filename = os.path.join(tmpdir, "mainfile.blend")
        bpy.ops.wm.save_as_mainfile(filepath=filename, check_existing=False, copy=True, relative_remap=True)
        ret = subprocess.call([sys.argv[0], "-b", "-noaudio", filename, "-P", __file__, "--", "repath"])
        if ret != 0:
            raise RuntimeError("Calling blender returned code {}".format(ret))
        self.report({'INFO'}, "mainfile.blend successfully exported: {}".format(filename))
        
        if scene.cycles.progressive == 'PATH':
            cost_per_bounce = scene.cycles.samples
        elif scene.cycles.progressive == 'BRANCHED_PATH':
            cost_per_bounce = scene.cycles.aa_samples * (
                scene.cycles.diffuse_samples +
                scene.cycles.glossy_samples +
                scene.cycles.transmission_samples +
                scene.cycles.ao_samples +
                scene.cycles.mesh_light_samples +
                scene.cycles.subsurface_samples)
        else:
            raise RuntimeError("Unknows sampling type: %s" % (scene.cycles.progressive))
        
        settings = scene.bitwrk_settings
        percentage = max(1, min(10000, scene.render.resolution_percentage))
        resx = int(scene.render.resolution_x * percentage / 100)
        resy = int(scene.render.resolution_y * percentage / 100)
        num_layers = 0
        for layer in scene.render.layers:
            if layer.use:
                num_layers += 1
        if scene.render.use_single_layer:
            num_layers=1
        is_multilayer = len(scene.render.layers) > 1 and not scene.render.use_single_layer
        cost_per_pixel = max(1, num_layers) * scene.cycles.max_bounces * cost_per_bounce
        
        max_pixels_per_tile = int(math.floor(get_max_cost(settings) / cost_per_pixel))
        tiles = self._makeTiles(settings, scene.frame_current, 0, 0, resx, resy, max_pixels_per_tile)
        # Sort by distance to center
        tiles.sort(key=lambda t: abs(t.minx + t.resx/2 - resx/2) + abs(t.miny + t.resy/2 - resy/2))
        
        num_active = 0
        while not self.test_break():        
        
            remaining = [t for t in tiles if not t.success]
            if not remaining:
                self.report({'INFO'}, "Successfully rendered {} tiles on BitWrk.".format(len(tiles)))
                break
                
            # Dispatch some unfinished tiles
            for tile in remaining:
                if tile.conn is None and num_active < settings.concurrency:
                    if tile.dispatch(settings, bpy.data, filename, self):
                        num_active += 1
            
            # Poll from all tiles currently active
            active = filter(lambda tile: tile.conn is not None, tiles)
            rlist, wlist, xlist = select.select(active, [], active, 2.0)
            
            # Collect from all tiles where data has arrived
            for list in rlist, xlist:
                for tile in list:
                    if tile.conn is not None:
                        tile.collect(settings, self, is_multilayer)
                        # collect has either failed or not. In any case, the tile is
                        # no longer active.
                        num_active -= 1
            
            # Report status
            successful = 0
            for tile in tiles:
                if tile.success:
                    successful += 1
            self.update_progress(successful / len(tiles))
        if self.test_break():
            for tile in filter(lambda tile: tile.conn is not None, tiles):
                tile.cancel()
    
    angle = 0.0
    @classmethod
    def _getcolor(cls):
        cls.angle += 0.61803399
        if cls.angle >= 1:
            cls.angle -= 1
        return colorsys.hsv_to_rgb(cls.angle, 0.5, 0.2)
        
        
    
    def _makeTiles(self, settings, frame, minx, miny, resx, resy, max_pixels):
        #print("make tiles:", minx, miny, resx, resy, max_pixels)
        pixels = resx*resy
        if pixels <= max_pixels:
            c = BitWrkRenderEngine._getcolor()
            tile = Tile(frame, minx, miny, resx, resy, [c[0], c[1], c[2], 1])
            return [tile]
        elif resx >= resy:
            left = resx // 2
            result = self._makeTiles(settings, frame, minx, miny, left, resy, max_pixels)
            result.extend(self._makeTiles(settings, frame, minx+left, miny, resx-left, resy, max_pixels))
            return result
        else:
            top = resy // 2
            result = self._makeTiles(settings, frame, minx, miny, resx, top, max_pixels)
            result.extend(self._makeTiles(settings, frame, minx, miny+top, resx, resy-top, max_pixels))
            return result

def resource_id(path):
    abspath = os.path.abspath(bpy.path.abspath(path))
    if type(abspath) != bytes:
        abspath = abspath.encode('utf-8')
    return hashlib.md5(abspath).hexdigest()

def resource_path(path):
    return"//rsrc." + resource_id(path) + ".data"

def object_abspath(obj):
    """Returns the absolute path of an object that is used to compute its
    resource id. Takes linked libraries into account, recursively."""
    if hasattr(obj, 'packed_file') and obj.packed_file:
        return
    if not obj.filepath:
        return
    path = obj.filepath
    while hasattr(obj, 'library') and obj.library:
        lib = obj.library
        if not lib.filepath:
            raise RuntimeExeption("Library without a filepath: " + lib)
        path = bpy.path.abspath(path, lib.filepath)
        obj = lib
    os.path.abspath(bpy.path.abspath(path))
    return path
    

# Called when this script is called as a main file with special parameters.
# Modifies all included paths to point to files named by the pattern
# '//rsrc.' + md5(absolute original path) + '.data'
# This method is called in a special blender session. 
def repath():
    def repath_obj(obj):
        obj.filepath = resource_path(object_abspath(p))
            
    for img in bpy.data.images:
        repath_obj(img)
        print("Image: "+img.filepath)
    for snd in bpy.data.sounds:
        repath_obj(snd)
        print("Sound: "+snd.filepath)
    for txt in bpy.data.texts:
        repath_obj(txt)
        print("Text: "+txt.filepath)
    for vid in bpy.data.movieclips:
        repath_obj(vid)
        print("Video: "+vid.filepath)
    for lib in bpy.data.libraries:
        repath_obj(lib)
        print("Library: "+lib.filepath)

def register():
    print("Registered BitWrk renderer")
    bpy.utils.register_class(BitWrkRenderEngine)
    bpy.utils.register_class(RENDER_PT_bitwrk_settings)
    bpy.utils.register_class(BitWrkSettings)
    bpy.utils.register_class(StartBrowserOperator)
    for name in dir(bpy.types):
        klass = getattr(bpy.types, name)
        if 'COMPAT_ENGINES' not in dir(klass):
            continue
        if 'CYCLES' not in klass.COMPAT_ENGINES:
            continue
        if 'BITWRK_RENDER' not in klass.COMPAT_ENGINES:
            klass.COMPAT_ENGINES.add('BITWRK_RENDER')
            print("Adding BITWRK_RENDER support to",name)
        else:
            print("Type",name,"already supports BITWRK_BLENDER")
        
        
    
def unregister():
    bpy.utils.unregister_class(StartBrowserOperator)
    bpy.utils.unregister_class(BitWrkSettings)
    bpy.utils.unregister_class(RENDER_PT_bitwrk_settings)
    bpy.utils.unregister_class(BitWrkRenderEngine)


if __name__ == "__main__":
    try:
        idx = sys.argv.index("--")
    except:
        idx = -1
    
    if idx < 0:
        # This allows us to run the addon script directly from blender's
        # text editor without having to install it.
        register()
    else:
        try:
            args = sys.argv[idx+1:]
            print("Args:", args)
            if len(args) > 0 and args[0] == 'repath':
                repath()
                bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath, check_existing=False)
        except:
            sys.exit(-1)
