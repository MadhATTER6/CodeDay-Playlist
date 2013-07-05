import os, sys, md5, time
if 'modules' not in sys.path: sys.path.append('modules')
import util
from sqlalchemy import create_engine, event
from sqlalchemy import Column, Integer, BigInteger, String, Boolean, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, backref

Base = declarative_base()
Session = sessionmaker()
class MultipleRootError(Exception): pass
class NoRootError(Exception): pass

# Paths are stored in cdp.db databases as if cdp.db resided in os.curdir
# In practice, the database is loaded from program.py, two levels above
# the database. This database controller must be aware of that when
# using os.path methods, etc. Thus path from the database root is called 'path'
# and path from os.curdir is called 'rel_path'.
path_to_root = None
root_join = lambda a: os.path.join(path_to_root, a)

def get_contents_hash(path):
    contents = os.listdir(path)
    mr_itchy = md5.md5()
    for node in contents:
        mr_itchy.update(node)
    return mr_itchy.hexdigest()

load_listener = lambda target, context: target.on_load()

class Folder(Base):
    __tablename__ = 'folders'

    path = Column(String, primary_key = True)
    root = Column(Boolean)
    
    parent_path = Column(String, ForeignKey('folders.path'))
    parent = relationship("Folder", remote_side = 'Folder.path',
                                    backref = 'children')
    hash = Column(String)

    def __init__(self, path, parent = None):
        self.path = path
        self.rel_path = root_join(path)
        if parent:
            self.parent = parent
        else:
            self.root = True
        self.hash = get_contents_hash(self.rel_path)

    @staticmethod
    def build(path = None, parent = None):
        rel_path = root_join(path or ".")
        folder = Folder(path or ".", parent)
        for node in os.listdir(rel_path):
            rel_node = os.path.join(rel_path, node)
            if path: node = os.path.join(path, node)
            if os.path.isfile(rel_node):
                node = File(node, folder)
                folder.files.append(node)
            else:
                node = Folder.build(node, folder)
        return folder

    def check_fs(self):
        return os.path.exists(self.rel_path) \
               and self.hash == get_contents_hash(self.rel_path)

    def on_load(self):
        self.rel_path = root_join(self.path)

    def __str__(self):
        return self.__repr__()

    def __repr__(self):
        return "<Folder: '%s'>" % self.path

event.listen(Folder, 'load', load_listener)

class File(Base):
    __tablename__ = 'files'

    path = Column(String, primary_key = True)
    supported = Column(Boolean)
    size = Column(BigInteger)
    
    track = Column(String)
    album = Column(String)
    artist = Column(String)
    album_artist = Column(String)

    artist_id = Column(Integer, ForeignKey("artists.id"))
    artist_ref = relationship("Artist", backref='songs')
    parent_path = Column(String, ForeignKey("folders.path"))
    parent = relationship("Folder", backref = 'files')

    def __init__(self, path, parent = None):
        self.path = path
        if parent:
            self.parent = parent
        if util.is_supported(root_join(path)):
            self.supported = True
            tags = util.get_metadata(root_join(path))
            self.artist = tags[0]
            self.album_artist = tags[1]
            self.album = tags[2]
            self.track = tags[3]
        else:
            self.supported = False
        self.size = os.path.getsize(root_join(self.path))

        self.rel_path = root_join(self.path)
        self.change_alerted = False

    def get_dict(self):
        return {'track' : self.track,
                'album' : self.album,
                'artist': self.artist}

    def check_fs(self):
        return os.path.exists(self.rel_path) \
               and os.path.getsize(self.rel_path) == self.size

    def on_load(self):
        self.rel_path = root_join(self.path)

    def __str__(self):
        return self.__repr__()

    def __repr__(self):
        return "<File: '%s'>" % self.path

event.listen(File, 'load', load_listener)

class Artist(Base):
    __tablename__ = 'artists'

    id = Column(Integer, primary_key = True)
    name = Column(String)

    def __init__(self, name):
        self.name = name

    def __str__(self):
        return `self`
    
    def __repr__(self):
        return "<Artist \"%s\" with %s song%s>" % (self.name, len(self.songs),
                                           '' if len(self.songs) == 1 else 's')


def connect(root_path):
    global root, path_to_root, session, engine
    path_to_root = root_path
    engine = create_engine('sqlite:///' + root_join('cdp.db'))
    session = Session(bind = engine)
    Base.metadata.bind = engine
    Base.metadata.create_all()
    print "Reading database."
    root_query = session.query(Folder).filter(Folder.root == True)
    if root_query[:1]:
        root = root_query[0]
    else:
        print "Builing database. This may take a while."
        build()
        
def build():
    global root
    Base.metadata.create_all()
    root = Folder.build()
    session.add(root)
    session.commit()
    for song in session.query(File).filter(File.supported == True):
        artist_query = session.query(Artist) \
                              .filter(Artist.name == song.album_artist)
        if artist_query[:1]:
            artist_query[0].songs.append(song)
        else:
            artist = Artist(song.album_artist)
            artist.songs.append(song)
            session.add(artist)
            session.commit()

def get_artists():
    return session.query(Artist)

def hard_reset():
    global session
    Base.metadata.drop_all()
    session.close()
    session = Session(bind = engine)
    print "Rebuilding database from scratch. This may take a while."
    build()

def scan(wait_seconds = (0.1)):
    def _scan(item, dirty = []):
        if not item.check_fs():
            dirty.append(item)
        time.sleep(wait_seconds)
        if type(item) == Folder:
            for i in item.children + item.files:
                _scan(i, dirty)
        return dirty
    return _scan(root)

if __name__ == "__main__":
    connect('music/main')
