"""An unreachable source CDN cannot permanently block an approved post."""
import io,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import content_distribution as d

class SourceMedia(unittest.TestCase):
    payload={'source_id':'post-1','title':'Approved title','text':'Approved text'}
    media=[{'type':'photo','url':'https://example.org/source.jpg'}]
    def test_network_failure_uses_the_existing_original_illustration_path(self):
        with patch('post_images.cached_image',return_value=None),patch('post_images.ensure_image',return_value='/tmp/illustration.jpg') as generate,patch.object(d.requests,'get',side_effect=d.requests.exceptions.ReadTimeout()):
            actual=d.add_post_image('wrongdev',self.payload,self.media)
        self.assertEqual(actual['image'],'/tmp/illustration.jpg')
        generate.assert_called_once_with('wrongdev','post-1','Approved title','Approved text')
    def test_reachable_source_photo_is_preserved_without_generation(self):
        raw=io.BytesIO();Image.new('RGB',(20,20),'blue').save(raw,format='JPEG')
        response=Mock(ok=True,headers={'content-type':'image/jpeg'},content=raw.getvalue())
        with tempfile.TemporaryDirectory() as tmp,patch.object(d,'BASE_DIR',Path(tmp)),patch('post_images.cached_image',return_value=None),patch('post_images.ensure_image') as generate,patch.object(d.requests,'get',return_value=response):
            actual=d.add_post_image('wrongdev',self.payload,self.media)
            self.assertEqual(Path(actual['image']).read_bytes(),raw.getvalue())
        generate.assert_not_called()

if __name__=='__main__':unittest.main()
