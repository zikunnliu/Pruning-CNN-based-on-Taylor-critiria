import torch
from torch.autograd import Variable
from torchvision import models
import sys
import numpy as np
import time


def replace_layers(model, i, indexes, layers):#indexes输入了一个要求替换的序号的list,i是当前的层号，
    if i in indexes:
        return layers[indexes.index(i)]
    return model[i]


def prune_vgg16_conv_layer(model, layer_index, filter_index): #第Layer_index层的第filter_index个filter会被减去
    #print(type(model)) #class VGG
    #print(model.features)   #features是VGG中的卷积层表示，是nn.Sequential类的实例,并且Sequential中的__init__()又继承了nn.module，其中绑定了_modules等属性
    #print(type(model.features._modules))  #将features用有序字典的形式表现出来
    #print(type(model.features._modules.items()))
    #print(model.features._modules)
    #print(model.features._modules.keys())
    #print(model.features._modules[str(layer_index)])
    conv = model.features._modules[str(layer_index)]#这里_modules中的键都是str型的，强行转换，现在找到了要进行剪枝的层
    # _, conv = model.features._modules.items()[layer_index]
    next_conv = None
    offset = 1

    while layer_index + offset < len(model.features._modules.items()):   #找conv的下一个卷积层
       # print(str(layer_index + offset))
        res = model.features._modules[str(layer_index + offset)]
        # res = model.features._modules.items()[layer_index + offset]
        # if isinstance(res[1], torch.nn.modules.conv.Conv2d):
        if isinstance(res, torch.nn.modules.conv.Conv2d):
            next_name, next_conv = str(layer_index + offset), res
            break
        offset = offset + 1

    if conv.bias is not None:
        bias_flag =True
    else:
        bias_flag=False

    new_conv = \
        torch.nn.Conv2d(in_channels=conv.in_channels, \
                        out_channels=conv.out_channels - 1,
                        kernel_size=conv.kernel_size, \
                        stride=conv.stride,
                        padding=conv.padding,
                        dilation=conv.dilation,
                        groups=conv.groups,
                        bias=bias_flag)

    old_weights = conv.weight.data.cpu().numpy()   #将其conv.weight为parameter类型，转化成numpy数组操作
    new_weights = new_conv.weight.data.cpu().numpy()

    new_weights[: filter_index, :, :, :] = old_weights[: filter_index, :, :, :]
    new_weights[filter_index:, :, :, :] = old_weights[filter_index + 1:, :, :, :]
    #new_conv.weight.data = torch.from_numpy(new_weights).cuda()
    new_conv.weight.data = torch.from_numpy(new_weights)   #再从numppy数组转化回tensor

    bias_numpy = conv.bias.data.cpu().numpy()

    bias = np.zeros(shape=(bias_numpy.shape[0] - 1), dtype=np.float32)
    bias[:filter_index] = bias_numpy[:filter_index]      #bias的维度和输出的维度一致（一维）这里减一
    bias[filter_index:] = bias_numpy[filter_index + 1:]
    #new_conv.bias.data = torch.from_numpy(bias).cuda()
    new_conv.bias.data = torch.from_numpy(bias)

    if not next_conv is None:
        next_new_conv = \
            torch.nn.Conv2d(in_channels=next_conv.in_channels - 1, \
                            out_channels=next_conv.out_channels, \
                            kernel_size=next_conv.kernel_size, \
                            stride=next_conv.stride,
                            padding=next_conv.padding,
                            dilation=next_conv.dilation,
                            groups=next_conv.groups,
                            bias=next_conv.bias)

        old_weights = next_conv.weight.data.cpu().numpy()
        new_weights = next_new_conv.weight.data.cpu().numpy()

        new_weights[:, : filter_index, :, :] = old_weights[:, : filter_index, :, :]
        new_weights[:, filter_index:, :, :] = old_weights[:, filter_index + 1:, :, :]
        #next_new_conv.weight.data = torch.from_numpy(new_weights).cuda()
        next_new_conv.weight.data = torch.from_numpy(new_weights)
        next_new_conv.bias.data = next_conv.bias.data

    if not next_conv is None:
        features = torch.nn.Sequential(
            *(replace_layers(model.features, i, [layer_index, layer_index + offset], \
                             [new_conv, next_new_conv]) for i, _ in enumerate(model.features)))   
        del model.features
        del conv

        model.features = features

    else:
        # Prunning the last conv layer. This affects the first linear layer of the classifier.
        model.features = torch.nn.Sequential(
            *(replace_layers(model.features, i, [layer_index], [new_conv]) for i, _ in enumerate(model.features)))
        layer_index = 0
        old_linear_layer = None
        for _, module in model.classifier._modules.items():
            if isinstance(module, torch.nn.Linear):
                old_linear_layer = module
                break
            layer_index = layer_index + 1

        if old_linear_layer is None:
            raise BaseException("No linear laye found in classifier")
        params_per_input_channel = old_linear_layer.in_features / conv.out_channels

        # print(old_linear_layer.in_features - params_per_input_channel)
        # print(old_linear_layer.out_features)
        new_linear_layer = \
            torch.nn.Linear(int(old_linear_layer.in_features - params_per_input_channel),
                            old_linear_layer.out_features)

        old_weights = old_linear_layer.weight.data.cpu().numpy()
        new_weights = new_linear_layer.weight.data.cpu().numpy()

        # print(filter_index * params_per_input_channel)
        new_weights[:, : int(filter_index * params_per_input_channel)] = \
            old_weights[:, : int(filter_index * params_per_input_channel)]
        new_weights[:, int(filter_index * params_per_input_channel):] = \
            old_weights[:, int((filter_index + 1) * params_per_input_channel):]

        new_linear_layer.bias.data = old_linear_layer.bias.data
        #new_linear_layer.weight.data = torch.from_numpy(new_weights).cuda()
        new_linear_layer.weight.data = torch.from_numpy(new_weights)

        classifier = torch.nn.Sequential(
            *(replace_layers(model.classifier, i, [layer_index], \
                             [new_linear_layer]) for i, _ in enumerate(model.classifier)))

        del model.classifier
        del next_conv
        del conv
        model.classifier = classifier

    return model


if __name__ == '__main__':
    model = models.vgg16(pretrained=True)
    #print(model)
    model.train()

    t0 = time.time()
    model =prune_vgg16_conv_layer(model, 28, 10)   #model为实例化的vgg16
    #print(model)
    print ("The prunning took", time.time() - t0)
