#!/usr/bin/python3

from os import mkdir;
from os.path import join, exists;
from shutil import rmtree;
from math import ceil;
from multiprocessing import Process;
from pycocotools.coco import COCO;
import numpy as np;
import cv2;
import tensorflow as tf;

PROCESS_NUM = 80;

def parse_function(serialized_example):

  feature = tf.io.parse_single_example(
    serialized_example,
    features = {
      'image': tf.io.FixedLenFeature((), dtype = tf.string, default_value = ''),
      'shape': tf.io.FixedLenFeature((3,), dtype = tf.int64),
      'label': tf.io.VarLenFeature(dtype = tf.float32)
    }
  );
  shape = tf.cast(feature['shape'], dtype = tf.int32);
  data = tf.io.decode_jpeg(feature['image']);
  data = tf.reshape(data, shape);
  data = tf.cast(data, dtype = tf.float32); # data.shape = (h, w, 3)
  label = tf.sparse.to_dense(feature['label'], default_value = 0);
  label = tf.reshape(label, (shape[0], shape[1])); # label.shape = (h, w)
  # 1) random hsv
  delta_h = tf.random.uniform(minval = -10, maxval = 10, shape = (), dtype = tf.int32);
  delta_s = tf.random.uniform(minval = -10, maxval = 10, shape = (), dtype = tf.int32);
  delta_v = tf.random.uniform(minval = -10, maxval = 10, shape = (), dtype = tf.int32);
  hsv = tf.image.rgb_to_hsv(tf.expand_dims(data, axis = 0)); # hsv.shape = (1, h, w, 3)
  h = hsv[...,0:1]; # h.shape = (1, h, w, 1)
  s = hsv[...,1:2]; # s.shape = (1, h, w, 1)
  v = hsv[...,2:3]; # v.shape = (1, h, w, 1)
  h = (h + delta_h) % 180; # h.shape = (1, h, w, 1)
  s = tf.clip_by_value(s + delta_s, 0, 255); # s.shape = (1, h, w, 1)
  v = tf.clip_by_value(v + delta_v, 0, 255); # v.shape = (1, h, w, 1)
  hsv = tf.concat([h,s,v], axis = -1); # hsv.shape = (1, h, w, 3)
  data = tf.image.hsv_to_rgb(hsv); # data.shape = (1, h, w, 3)
  # 2) random flip
  comp = tf.concat([data, tf.reshape(label, (1, tf.shape(label)[0], tf.shape(label)[1], 1))], axis = -1); # comp.shape = (1, h, w, 3 + 1)
  comp = tf.cond(tf.math.greater(tf.random.uniform(shape = ()), 0.5), lambda: comp, lambda: tf.image.flip_left_right(comp)); # comp.shape = (1, h, w, 3 + 1)
  data = comp[...,:-1]; # data.shape = (1, h, w, 3)
  label = comp[...,-1:]; # label.shape = (1, h, w, 1)
  # 3) random scale
  scale = tf.random.uniform(minval = 0.5, maxval = 2.0, shape = (), dtype = tf.float32);
  shape = tf.cast([float(data.shape[1]) * scale, float(data.shape[2]) * scale], dtype = tf.int32);
  data = tf.image.resize(data, shape, method = tf.image.ResizeMethod.BICUBIC); # data.shape = (1, s*h, s*w, 3)
  label = tf.image.resize(label, shape, method = tf.image.ResizeMethod.NEAREST_NEIGHBOR); # label.shape = (1, s*h, s*w, 1)
  # 4) random crop
  comp = tf.concat([data, albel], axis = -1); # comp.shape = (1, s*h, s*w, 3+1)
  crop_h = tf.math.mimimum(tf.shape(comp)[1], 512);
  crop_w = tf.math.minimum(tf.shape(comp)[2], 512);
  crop_c = tf.shape(comp)[3];
  comp = tf.image.random_crop(comp, (1, crop_h, crop_w, crop_c)); # data.shape = (1, min(512, s*h), min(512, s*w), 3+1)
  data = comp[...,:-1]; # data.shape = (1, min(512, s*h), min(512, s*w), 3)
  label = comp[...,-1:]; # label.shape = (1, min(512, s*h), min(512, s*w), 1)
  # 5) rescale to 512x512
  data = tf.image.resize(data, (512, 512), method = tf.image.ResizeMethod.BICUBIC); # data.shape = (1, 512, 512, 3)
  label = tf.image.resize(label, (512, 512), method = tf.image.ResizeMethod.NEAREST_NEIGHBOR); # label.shape = (1, 512, 512, 1)
  # 6) squeeze
  data = tf.squeeze(data, axis = 0); # data.shape = (512, 512, 3)
  label = tf.reshape(label, (tf.shape(label)[1], tf.shape(label)[2])); # label.shape = (512, 512)
  return data, label;

def create_dataset(image_dir, label_dir, trainset = True):

  anno = COCO(join(label_dir, 'instances_train2017.json' if trainset else 'instances_val2017.json'));
  if exists('trainset' if trainset else 'testset'): rmtree('trainset' if trainset else 'testset');
  mkdir('trainset' if trainset else 'testset');
  imgs_for_each = ceil(len(anno.getImgIds()) / PROCESS_NUM);
  handlers = list();
  filenames = list();
  for i in range(PROCESS_NUM):
    filename = ('trainset_part_%d' if trainset else 'testset_part_%d') % i;
    filenames.append(join('trainset' if trainset else 'testset', filename));
    handlers.append(Process(target = worker, args = (join('trainset' if trainset else 'testset', filename), anno, image_dir, anno.getImgIds()[i * imgs_for_each:(i+1) * imgs_for_each] if i != PROCESS_NUM - 1 else anno.getImgIds()[i * imgs_for_each:])));
    handlers[-1].start();
  for handler in handlers:
    handler.join();

def worker(filename, anno, image_dir, image_ids):
  writer = tf.io.TFRecordWriter(filename);
  for image in image_ids:
    img_info = anno.loadImgs([image])[0];
    img = cv2.imread(join(image_dir, img_info['file_name']));
    if img is None:
      print('can\'t open image %s' % (join(image_dir, img_info['file_name'])));
      continue;
    mask = np.zeros((img_info['height'], img_info['width']));
    for category in anno.getCatIds():
      annIds = anno.getAnnIds(imgIds = image, catIds = category);
      anns = anno.loadAnns(annIds);
      for ann in anns:
        # for every instance of category in current image
        instance_mask = anno.annToMask(ann);
        mask = np.maximum(mask, instance_mask * category);
    trainsample = tf.train.Example(features = tf.train.Features(
      feature = {
        'image': tf.train.Feature(bytes_list = tf.train.BytesList(value = [tf.io.encode_jpeg(img).numpy()])),
        'shape': tf.train.Feature(int64_list = tf.train.Int64List(value = list(img.shape))),
        'label': tf.train.Feature(float_list = tf.train.FloatList(value = tf.reshape(mask, (-1,))))
      }
    ));
    writer.write(trainsample.SerializeToString());
  writer.close();

if __name__ == "__main__":

  assert tf.executing_eagerly();
  from sys import argv;
  if len(argv) != 4:
    print('Usage: %s <train image dir> <test image dir> <anno dir>' % (argv[0]));
    exit(1);
  create_dataset(argv[2], argv[3], False);
  create_dataset(argv[1], argv[3], True);
