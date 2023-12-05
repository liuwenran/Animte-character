import os
import random

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision import transforms
from torchvision.transforms import functional as TF
import PIL
from PIL import Image
from transformers import CLIPImageProcessor
import json
from torch.utils.data import IterableDataset, Dataset
from tqdm import tqdm
import jsonlines


class LaionHumanSD(Dataset):
    def __init__(self, json_file, tokenizer, size=(512, 512), t_drop_rate=0.05, i_drop_rate=0.05, ti_drop_rate=0.05):
        super().__init__()
        self.data_root = '/mnt/petrelfs/majie/project/HumanSD/humansd_data/datasets/Laion'
        with open(json_file) as f:
            data = json.load(f)
        self.data = self.construct_data(data)
        print(f'Dataset size: {len(self.data)}')

        self.tokenizer = tokenizer
        self.size = size
        self.t_drop_rate = t_drop_rate
        self.i_drop_rate = i_drop_rate
        self.ti_drop_rate = ti_drop_rate

        # TODO: support ARB bucket
        self.transform = transforms.Compose([
            transforms.Resize(self.size, interpolation=transforms.InterpolationMode.BILINEAR),
            transforms.CenterCrop(self.size),
            transforms.ToTensor(),
            transforms.Normalize([0.5], [0.5]),
        ])
        
        self.clip_image_processor = CLIPImageProcessor()

    def construct_data(self, data):
        keys = list(data.keys())
        values = list(data.values())
        filter_data = []
        for i in tqdm(range(len(values))):
            each = values[i]
            each['key'] = keys[i]
            each['img_path'] = os.path.join(self.data_root, each['img_path'])
            filter_data.append(each)
        return filter_data

    def remove_not_exist(self, data):
        keys = list(data.keys())
        values = list(data.values())
        filter_data = []
        for i in tqdm(range(len(values))):
            each = values[i]
            each['key'] = keys[i]
            each['img_path'] = os.path.join(self.data_root, each['img_path'])
            if os.path.exists(each['img_path']):
                filter_data.append(each)
        return filter_data
        
    def __getitem__(self, idx):
        try:
            item = self.data[idx]
            image_file = item["img_path"]
            raw_image = Image.open(image_file).convert("RGB")
            prompt = item["prompt"]
            image = self.transform(raw_image)
            clip_image = self.clip_image_processor(images=raw_image, return_tensors="pt").pixel_values
        except Exception:
            return self.__getitem__((idx + 1) % len(self.data))

        drop_image_embed = 0
        rand_num = random.random()
        if rand_num < self.i_drop_rate:
            drop_image_embed = 1
        elif rand_num < (self.i_drop_rate + self.t_drop_rate):
            prompt = ""
        elif rand_num < (self.i_drop_rate + self.t_drop_rate + self.ti_drop_rate):
            prompt = ""
            drop_image_embed = 1

        text_input_ids = self.tokenizer(
            prompt,
            max_length=self.tokenizer.model_max_length,
            padding="max_length",
            truncation=True,
            return_tensors="pt"
        ).input_ids
        
        return {
            'image': image,
            'text_input_ids': text_input_ids,
            'clip_image': clip_image,
            "drop_image_embed": drop_image_embed,
        }

    def __len__(self):
        return len(self.data)


class BaseDataset(Dataset):
    def __init__(self, json_file, tokenizer, control_type='canny') -> None:
        super().__init__()
        self.tokenizer = tokenizer
        self.short_size = 512
        self.control_type = control_type

        self.data = self.construct_data(json_file)

        # self.image_transform = transforms.Compose([
        #     transforms.Resize(self.short_size, interpolation=transforms.InterpolationMode.BILINEAR),
        #     transforms.CenterCrop(self.short_size),
        #     # transforms.RandomCrop(self.short_size),
        #     transforms.ToTensor(),
        #     transforms.Normalize([0.5], [0.5]),
        # ])

        # self.transform = transforms.Compose([
        #     transforms.Resize(self.short_size, interpolation=transforms.InterpolationMode.BILINEAR),
        #     transforms.RandomCrop(self.short_size),
        #     transforms.ToTensor(),
        #     transforms.Normalize([0.5], [0.5]),
        # ])

        # self.control_transform = transforms.Compose([
        #     transforms.Resize(self.short_size, interpolation=transforms.InterpolationMode.BILINEAR),
        #     transforms.RandomCrop(self.short_size),
        #     transforms.ToTensor(),
        # ])

    def construct_data(self, json_file_list):
        if type(json_file_list) == str:
            json_file_list = [json_file_list]

        data = []
        for json_file in json_file_list:
            with jsonlines.open(json_file) as reader:
                for each in reader:
                    data.append(each)
        return data
    
    def image_transform(self, image):
        image = transforms.Resize(self.short_size, interpolation=transforms.InterpolationMode.BILINEAR)(image)
        image = transforms.CenterCrop(self.short_size)(image)

        i, j, h, w = transforms.RandomCrop.get_params(image, output_size=(512, 512))
        # image = TF.crop(image, i, j, h, w)

        image = transforms.ToTensor()(image)
        image = transforms.Normalize([0.5], [0.5])(image)

        return image, i, j, h, w
    
    def reference_transform(self, reference):
        reference = transforms.Resize(self.short_size, interpolation=transforms.InterpolationMode.BILINEAR)(reference)
        reference = transforms.CenterCrop(self.short_size)(reference)

        # i, j, h, w = transforms.RandomCrop.get_params(reference, output_size=(512, 512))
        # reference = TF.crop(reference, i, j, h, w)
        
        reference = transforms.ToTensor()(reference)
        reference = transforms.Normalize([0.5], [0.5])(reference)

        return reference

    def control_transform(self, control_image, i, j, h, w):
        control_image = transforms.Resize(self.short_size, interpolation=transforms.InterpolationMode.BILINEAR)(control_image)
        control_image = transforms.CenterCrop(self.short_size)(control_image)
        # control_image = TF.crop(control_image, i, j, h, w)
        control_image = transforms.ToTensor()(control_image)
        return control_image

    def __getitem__(self, idx):
        item = self.data[idx]
        image = Image.open(item['image']).convert("RGB")
        reference = Image.open(item['reference']).convert("RGB")
        
        if self.control_type == 'canny':
            control_image = Image.open(item['canny']).convert("RGB")
        elif self.control_type == 'pose':
            control_image = Image.open(item['pose']).convert("RGB")
        else:
            raise NotImplementedError

        if 'prompt' not in item or item['prompt'] == '':
            prompt = 'best quality,high quality'
        else:
            prompt = item['prompt']
        
        width, height = image.size
        width = (width // 8) * 8
        height = (height // 8) * 8

        image = image.resize((width, height))
        reference = reference.resize((width, height))
        control_image = control_image.resize((width, height))

        # print(f'Image size: {image.size}')
        # print(f'Reference size: {reference.size}')
        # print(f'Control image size: {control_image.size}')

        image, i, j, h, w = self.image_transform(image)
        reference = self.reference_transform(reference)
        control_image = self.control_transform(control_image, i, j, h, w)

        # print(f'Image shape: {image.shape}')
        # print(f'Reference shape: {reference.shape}')
        # print(f'Control image shape: {control_image.shape}')

        text_input_ids = self.tokenizer(
            prompt,
            max_length=self.tokenizer.model_max_length,
            padding="max_length",
            truncation=True,
            return_tensors="pt"
        ).input_ids
        
        return {
            'image': image,
            'text_input_ids': text_input_ids,
            'reference': reference,
            'control_image': control_image,
        }

    def __len__(self) -> int:
        return len(self.data)


class CCTVDataset(BaseDataset):
    pass


class TikTokDataset(BaseDataset):
    pass
    


if __name__ == '__main__':
    dataset = LaionHumanSD('/mnt/petrelfs/majie/project/HumanSD/humansd_data/datasets/Laion/Aesthetics_Human/mapping_file_training.json', None)
