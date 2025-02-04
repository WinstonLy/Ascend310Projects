import os
import numpy as np
import argparse
import time
import torch
import torchvision
import cv2


def yolo_forward_dynamic(output, num_classes, anchors, num_anchors, scale_x_y):
    # Output would be invalid if it does not satisfy this assert
    # assert (output.size(1) == (5 + num_classes) * num_anchors)

    # print(output.size())

    # Slice the second dimension (channel) of output into:
    # [ 2, 2, 1, num_classes, 2, 2, 1, num_classes, 2, 2, 1, num_classes ]
    # And then into
    # bxy = [ 6 ] bwh = [ 6 ] det_conf = [ 3 ] cls_conf = [ num_classes * 3 ]
    # batch = output.size(0)
    # H = output.size(2)
    # W = output.size(3)

    bxy_list = []
    bwh_list = []
    det_confs_list = []
    cls_confs_list = []

    for i in range(num_anchors):
        begin = i * (5 + num_classes)
        end = (i + 1) * (5 + num_classes)

        bxy_list.append(output[:, begin: begin + 2])
        bwh_list.append(output[:, begin + 2: begin + 4])
        det_confs_list.append(output[:, begin + 4: begin + 5])
        cls_confs_list.append(output[:, begin + 5: end])
   
    # Shape: [batch, num_anchors * 2, H, W]
    bxy = torch.cat(bxy_list, dim=1)

    # Shape: [batch, num_anchors * 2, H, W]
    bwh = torch.cat(bwh_list, dim=1)

    # Shape: [batch, num_anchors, H, W]
    det_confs = torch.cat(det_confs_list, dim=1)
    # Shape: [batch, num_anchors * H * W]
    # print(output.size(0),num_anchors * output.size(2) * output.size(3))
    det_confs = det_confs.view(output.size(0), num_anchors * output.size(2) * output.size(3))

    # Shape: [batch, num_anchors * num_classes, H, W]
    cls_confs = torch.cat(cls_confs_list, dim=1)
    # Shape: [batch, num_anchors, num_classes, H * W]
    print(num_anchors, output.size(0), output.size(2), output.size(3)) 
    cls_confs = cls_confs.view(output.size(0), num_anchors, num_classes, output.size(2) * output.size(3))
    # Shape: [batch, num_anchors, num_classes, H * W] --> [batch, num_anchors * H * W, num_classes]
    cls_confs = cls_confs.permute(0, 1, 3, 2).reshape(output.size(0), num_anchors * output.size(2) * output.size(3),
                                                      num_classes)

    # Apply sigmoid(), exp() and softmax() to slices
    print(bxy)
    bxy = torch.sigmoid(bxy) * scale_x_y - 0.5 * (scale_x_y - 1)
    bwh = torch.exp(bwh)
    det_confs = torch.sigmoid(det_confs)
    cls_confs = torch.sigmoid(cls_confs)

    # Prepare C-x, C-y, P-w, P-h (None of them are torch related)
    grid_x = np.expand_dims(np.expand_dims(
        np.expand_dims(np.linspace(0, output.size(3) - 1, output.size(3)), axis=0).repeat(output.size(2), 0), axis=0),
                            axis=0)
    grid_y = np.expand_dims(np.expand_dims(
        np.expand_dims(np.linspace(0, output.size(2) - 1, output.size(2)), axis=1).repeat(output.size(3), 1), axis=0),
                            axis=0)
    # grid_x = torch.linspace(0, W - 1, W).reshape(1, 1, 1, W).repeat(1, 1, H, 1)
    # grid_y = torch.linspace(0, H - 1, H).reshape(1, 1, H, 1).repeat(1, 1, 1, W)

    anchor_w = []
    anchor_h = []
    for i in range(num_anchors):
        anchor_w.append(anchors[i * 2])
        anchor_h.append(anchors[i * 2 + 1])

    device = None
    cuda_check = output.is_cuda
    if cuda_check:
        device = output.get_device()

    bx_list = []
    by_list = []
    bw_list = []
    bh_list = []

    # Apply C-x, C-y, P-w, P-h
    for i in range(num_anchors):
        ii = i * 2
        # Shape: [batch, 1, H, W]
        bx = bxy[:, ii: ii + 1] + torch.tensor(grid_x, device=device,
                                               dtype=torch.float32)  # grid_x.to(device=device, dtype=torch.float32)
        # Shape: [batch, 1, H, W]
        by = bxy[:, ii + 1: ii + 2] + torch.tensor(grid_y, device=device,
                                                   dtype=torch.float32)  # grid_y.to(device=device, dtype=torch.float32)
        # Shape: [batch, 1, H, W]
        bw = bwh[:, ii: ii + 1] * anchor_w[i]
        # Shape: [batch, 1, H, W]
        bh = bwh[:, ii + 1: ii + 2] * anchor_h[i]

        bx_list.append(bx)
        by_list.append(by)
        bw_list.append(bw)
        bh_list.append(bh)

    ########################################
    #   Figure out bboxes from slices     #
    ########################################

    # Shape: [batch, num_anchors, H, W]
    bx = torch.cat(bx_list, dim=1)
    # Shape: [batch, num_anchors, H, W]
    by = torch.cat(by_list, dim=1)
    # Shape: [batch, num_anchors, H, W]
    bw = torch.cat(bw_list, dim=1)
    # Shape: [batch, num_anchors, H, W]
    bh = torch.cat(bh_list, dim=1)

    # Shape: [batch, 2 * num_anchors, H, W]
    bx_bw = torch.cat((bx, bw), dim=1)
    # Shape: [batch, 2 * num_anchors, H, W]
    by_bh = torch.cat((by, bh), dim=1)

    # normalize coordinates to [0, 1]
    bx_bw /= output.size(3)
    by_bh /= output.size(2)

    # Shape: [batch, num_anchors * H * W, 1]
    bx = bx_bw[:, :num_anchors].view(output.size(0), num_anchors * output.size(2) * output.size(3), 1)
    by = by_bh[:, :num_anchors].view(output.size(0), num_anchors * output.size(2) * output.size(3), 1)
    bw = bx_bw[:, num_anchors:].view(output.size(0), num_anchors * output.size(2) * output.size(3), 1)
    bh = by_bh[:, num_anchors:].view(output.size(0), num_anchors * output.size(2) * output.size(3), 1)

    bx1 = bx - bw * 0.5
    by1 = by - bh * 0.5
    bx2 = bx1 + bw
    by2 = by1 + bh

    # Shape: [batch, num_anchors * h * w, 4] -> [batch, num_anchors * h * w, 1, 4]
    boxes = torch.cat((bx1, by1, bx2, by2), dim=2).view(output.size(0), num_anchors * output.size(2) * output.size(3),
                                                        1, 4)
    # boxes = boxes.repeat(1, 1, num_classes, 1)

    # boxes:     [batch, num_anchors * H * W, 1, 4]
    # cls_confs: [batch, num_anchors * H * W, num_classes]
    # det_confs: [batch, num_anchors * H * W]

    det_confs = det_confs.view(output.size(0), num_anchors * output.size(2) * output.size(3), 1)
    confs = cls_confs * det_confs

    # boxes: [batch, num_anchors * H * W, 1, 4]
    # confs: [batch, num_anchors * H * W, num_classes]

    return boxes, confs


class YoloLayer(object):
    ''' Yolo layer
    model_out: while inference,is post-processing inside or outside the model
        true:outside
    '''
    def __init__(self, anchor_mask=[], num_classes=0, anchors=[], num_anchors=1, stride=32, model_out=False):

        self.anchor_mask = anchor_mask
        self.num_classes = num_classes
        self.anchors = anchors
        self.num_anchors = num_anchors
        self.anchor_step = len(anchors) // num_anchors
        self.coord_scale = 1
        self.noobject_scale = 1
        self.object_scale = 5
        self.class_scale = 1
        self.thresh = 0.6
        self.stride = stride
        self.seen = 0
        self.scale_x_y = 1

        self.model_out = model_out


    def forward(self, output):
        masked_anchors = []

        for m in self.anchor_mask:
            masked_anchors += self.anchors[m * self.anchor_step:(m + 1) * self.anchor_step]
        masked_anchors = [anchor / self.stride for anchor in masked_anchors]
        print(masked_anchors)
        return yolo_forward_dynamic(output, self.num_classes, masked_anchors,
                                    len(self.anchor_mask), scale_x_y=self.scale_x_y)


def get_region_boxes(boxes_and_confs):
    # print('Getting boxes from boxes and confs ...')

    boxes_list = []
    confs_list = []

    for item in boxes_and_confs:
        boxes_list.append(item[0])
        confs_list.append(item[1])

    # boxes: [batch, num1 + num2 + num3, 1, 4]
    # confs: [batch, num1 + num2 + num3, num_classes]
    boxes = torch.cat(boxes_list, dim=1)
    confs = torch.cat(confs_list, dim=1)

    return [boxes, confs]


def nms_cpu(boxes, confs, nms_thresh=0.5, min_mode=False):
    # print(boxes.shape)
    x1 = boxes[:, 0]
    y1 = boxes[:, 1]
    x2 = boxes[:, 2]
    y2 = boxes[:, 3]

    areas = (x2 - x1) * (y2 - y1)
    order = confs.argsort()[::-1]

    keep = []
    while order.size > 0:
        idx_self = order[0]
        idx_other = order[1:]

        keep.append(idx_self)

        xx1 = np.maximum(x1[idx_self], x1[idx_other])
        yy1 = np.maximum(y1[idx_self], y1[idx_other])
        xx2 = np.minimum(x2[idx_self], x2[idx_other])
        yy2 = np.minimum(y2[idx_self], y2[idx_other])

        w = np.maximum(0.0, xx2 - xx1)
        h = np.maximum(0.0, yy2 - yy1)
        inter = w * h

        if min_mode:
            over = inter / np.minimum(areas[order[0]], areas[order[1:]])
        else:
            over = inter / (areas[order[0]] + areas[order[1:]] - inter)

        inds = np.where(over <= nms_thresh)[0]
        order = order[inds + 1]

    return np.array(keep)


def nms(conf_thresh, nms_thresh, output):
    # anchors = [12, 16, 19, 36, 40, 28, 36, 75, 76, 55, 72, 146, 142, 110, 192, 243, 459, 401]
    # num_anchors = 9
    # anchor_masks = [[0, 1, 2], [3, 4, 5], [6, 7, 8]]
    # strides = [8, 16, 32]
    # anchor_step = len(anchors) // num_anchors

    # [batch, num, 1, 4]
    box_array = output[0]
    # [batch, num, num_classes]
    confs = output[1]

    if type(box_array).__name__ != 'ndarray':
        box_array = box_array.cpu().detach().numpy()
        confs = confs.cpu().detach().numpy()

    num_classes = confs.shape[2]

    # [batch, num, 4]
    box_array = box_array[:, :, 0]

    # [batch, num, num_classes] --> [batch, num]
    max_conf = np.max(confs, axis=2)
    max_id = np.argmax(confs, axis=2)


    bboxes_batch = []
    for i in range(box_array.shape[0]):

        argwhere = max_conf[i] > conf_thresh
        l_box_array = box_array[i, argwhere, :]
        l_max_conf = max_conf[i, argwhere]
        l_max_id = max_id[i, argwhere]

        bboxes = []
        # nms for each class
        for j in range(num_classes):

            cls_argwhere = l_max_id == j
            ll_box_array = l_box_array[cls_argwhere, :]
            ll_max_conf = l_max_conf[cls_argwhere]
            ll_max_id = l_max_id[cls_argwhere]

            keep = nms_cpu(ll_box_array, ll_max_conf, nms_thresh)

            if (keep.size > 0):
                ll_box_array = ll_box_array[keep, :]
                ll_max_conf = ll_max_conf[keep]
                ll_max_id = ll_max_id[keep]

                for k in range(ll_box_array.shape[0]):
                    bboxes.append(
                        [ll_box_array[k, 0], ll_box_array[k, 1], ll_box_array[k, 2], ll_box_array[k, 3],
                         ll_max_conf[k], ll_max_id[k]])

        bboxes_batch.append(bboxes)

    return bboxes_batch


def post_process(flags):
    names = np.loadtxt(flags.coco_class_names, dtype='str', delimiter='\n')

    # 读取bin文件用于生成预测结果
    bin_path = flags.bin_data_path
    ori_path = flags.origin_jpg_path

    anchors = [12, 16, 19, 36, 40, 28, 36, 75, 76, 55, 72, 146, 142, 110, 192, 243, 459, 401]
    num_classes = 80

    det_results_path = flags.det_results_path
    os.makedirs(det_results_path, exist_ok=True)
    total_img = set([name[:name.rfind('_')] for name in os.listdir(bin_path) if "bin" in name])


    yolo1 = YoloLayer(anchor_mask=[0, 1, 2], num_classes=num_classes, anchors=anchors, num_anchors=9, stride=8)
    yolo2 = YoloLayer(anchor_mask=[3, 4, 5], num_classes=num_classes, anchors=anchors, num_anchors=9, stride=16)
    yolo3 = YoloLayer(anchor_mask=[6, 7, 8], num_classes=num_classes, anchors=anchors, num_anchors=9, stride=32)

    yolo_shape = [[1, 255, 76, 76], [1, 255, 38, 38], [1, 255, 19, 19]]

    for bin_file in sorted(total_img):
        path_base = os.path.join(bin_path, bin_file)
        print(path_base)
        # print('\n', os.path.join(ori_path, '{}.jpg'.format(bin_file)), '\n')
        src_img = cv2.imread(os.path.join(ori_path, '{}.jpg'.format(bin_file)))
        assert src_img is not None, 'Image Not Found ' + bin_file

        # 加载检测的所有输出tensor
        feature_map_1 = np.fromfile(path_base + "_" + '1' + ".bin", dtype="float32").reshape(yolo_shape[0])
        feature_map_2 = np.fromfile(path_base + "_" + '2' + ".bin", dtype="float32").reshape(yolo_shape[1])
        feature_map_3 = np.fromfile(path_base + "_" + '3' + ".bin", dtype="float32").reshape(yolo_shape[2])
    
        pred_1 = yolo1.forward(torch.from_numpy(feature_map_1))
        pred_2 = yolo2.forward(torch.from_numpy(feature_map_2))
        pred_3 = yolo3.forward(torch.from_numpy(feature_map_3))

        # nms
        output = get_region_boxes([pred_1, pred_2, pred_3])
        pred = nms(conf_thresh=0.4, nms_thresh=0.6, output=output)[0]

        # save result
        det_results_file = os.path.join(det_results_path, bin_file + ".txt")
        print(det_results_file)
        with open(det_results_file, 'w') as f:
            width = src_img.shape[1]
            height = src_img.shape[0]
            for i in range(len(pred)):
                box = pred[i]
                x1 = int(box[0] * width)
                y1 = int(box[1] * height)
                x2 = int(box[2] * width)
                y2 = int(box[3] * height)
                cls_conf = box[4]
                cls_id = box[5]

                content = '{} {} {} {} {} {}'.format(names[int(cls_id)], cls_conf, x1, y1, x2, y2)
                print(content)
                f.write(content)
                f.write('\n')



if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--bin_data_path", default="./result/dumpOutput_device0")
    parser.add_argument("--origin_jpg_path", default="./val2014/")
    parser.add_argument("--det_results_path",
                        default="./detection-results/")
    parser.add_argument("--coco_class_names", default="./coco2014.names")
    parser.add_argument("--net_out_num", default=3)
    flags = parser.parse_args()

    post_process(flags)
