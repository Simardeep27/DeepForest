'''
Preprocess data
Loading, Standardizing and Filtering the raw data to dimish false positive labels 
'''

import pandas as pd
import glob
import os
import random
import xmltodict
import numpy as np
import rasterio
from PIL import Image
import slidingwindow as sw
import itertools

def load_csvs(h5_dir):
    """
    Read preprocessed csv files generated by Generate.py run method
    """
    
    #If a single file, read, if a a dir, loop through files
    if os.path.isdir(h5_dir):
        #Gather list of csvs
        data_paths=glob.glob(h5_dir+"/*.csv")
        dataframes = (pd.read_csv(f,index_col=None) for f in data_paths)
        data = pd.concat(dataframes, ignore_index=True)      
        
    else:
        data=pd.read_csv(h5_dir)

    return data
    
def load_data(data_dir,res,lidar_path):
    '''
    data_dir: path to .csv files. Optionall can be a path to a specific .csv file.
    res: Cell resolution of the rgb imagery
    '''
    
    if(os.path.splitext(data_dir)[-1]==".csv"):
        data=pd.read_csv(data_dir,index_col=0)
    else:
        #Gather list of csvs
        data_paths=glob.glob(data_dir+"/*.csv")
        dataframes = (pd.read_csv(f,index_col=0) for f in data_paths)
        data = pd.concat(dataframes, ignore_index=False)
    
    #Modify indices, which came from R, zero indexed in python
    data=data.set_index(data.index.values-1)
    data.numeric_label=data.numeric_label-1
    
    #Remove xmin==xmax
    data=data[data.xmin!=data.xmax]    
    data=data[data.ymin!=data.ymax]    

    ##Create bounding coordinates with respect to the crop for each box
    #Rescaled to resolution of the cells.Also note that python and R have inverse coordinate Y axis, flipped rotation.
    data['origin_xmin']=(data['xmin']-data['tile_xmin'])/res
    data['origin_xmax']=(data['xmin']-data['tile_xmin']+ data['xmax']-data['xmin'])/res
    data['origin_ymin']=(data['tile_ymax']-data['ymax'])/res
    data['origin_ymax']= (data['tile_ymax']-data['ymax']+ data['ymax'] - data['ymin'])/res  
        
    #Check for lidar tiles
    data=check_for_lidar(data=data,lidar_path=lidar_path)
    
    #Check for remaining data
    assert(data.shape[0] > 0),"No training data remaining after ingestion, check lidar paths"
    
    return(data)
    
def zero_area(data):
    data=data[data.xmin!=data.xmax]    
    return(data)

def load_xml(path,res):

    #parse
    with open(path) as fd:
        doc = xmltodict.parse(fd.read())
    
    #grab objects
    tile_xml=doc["annotation"]["object"]
    
    xmin=[]
    xmax=[]
    ymin=[]
    ymax=[]
    label=[]
    
    if type(tile_xml) == list:
        
        treeID=np.arange(len(tile_xml))
        
        #Construct frame if multiple trees
        for tree in tile_xml:
            xmin.append(tree["bndbox"]["xmin"])
            xmax.append(tree["bndbox"]["xmax"])
            ymin.append(tree["bndbox"]["ymin"])
            ymax.append(tree["bndbox"]["ymax"])
            label.append(tree['name'])
    else:
        
        #One tree
        treeID=0
        
        xmin.append(tile_xml["bndbox"]["xmin"])
        xmax.append(tile_xml["bndbox"]["xmax"])
        ymin.append(tile_xml["bndbox"]["ymin"])
        ymax.append(tile_xml["bndbox"]["ymax"])
        label.append(tile_xml['name'])        
        
    rgb_path=doc["annotation"]["filename"]
    
    #bounds
    
    #read in tile to get dimensions
    full_path=os.path.join("data",doc["annotation"]["folder"] ,rgb_path)

    with rasterio.open(full_path) as dataset:
        bounds=dataset.bounds         
    
    #TODO find lidar path for annotations
    
    frame=pd.DataFrame({"treeID":treeID,"xmin":xmin,"xmax":xmax,"ymin":ymin,"ymax":ymax,"rgb_path":rgb_path,"label":label,
                        "numeric_label":0,
                        "tile_xmin":bounds.left,
                        "tile_xmax":bounds.right,
                        "tile_ymin":bounds.bottom,
                        "tile_ymax":bounds.top})

    #Modify indices, which came from R, zero indexed in python
    frame=frame.set_index(frame.index.values)

    ##Match expectations of naming, no computation needed for hand annotations
    frame['origin_xmin']=frame["xmin"].astype(float)
    frame['origin_xmax']=frame["xmax"].astype(float)
    frame['origin_ymin']=frame["ymin"].astype(float)
    frame['origin_ymax']= frame["ymax"].astype(float)
    
    return(frame)

def compute_windows(image,pixels=250,overlap=0.05):
    try:
        im = Image.open(image)
    except:
        return None
    numpy_image = np.array(im)    
    windows = sw.generate(numpy_image, sw.DimOrder.HeightWidthChannel, pixels,overlap)
    
    return(windows)

def retrieve_window(numpy_image,index,windows):
    crop=numpy_image[windows[index].indices()]
    return(crop)

def expand_grid(data_dict):
    rows = itertools.product(*data_dict.values())
    return pd.DataFrame.from_records(rows, columns=data_dict.keys())

def check_for_lidar(data,lidar_path):
    lidar_tiles=data.lidar_path.unique()
    
    lidar_exists=[]
    for x in list(lidar_tiles):
        does_exist=os.path.exists(os.path.join(lidar_path,x))
        lidar_exists.append(does_exist)
    
    #Filter data based on matching lidar tiles
    matching_lidar=list(lidar_tiles[lidar_exists])
    data=data[data.lidar_path.isin(matching_lidar)]
    
    return data

def split_training(csv_data,DeepForest_config,experiment):
    
    '''
    Divide windows into training and testing split.
    '''
    
    #reduce the data frame into tiles and windows
    windowdf=csv_data[["tile","window"]]
    data=windowdf.drop_duplicates()
    
    #More than one tile in training data?
    single_tile =  len(data.tile.unique()) == 1
    
    if single_tile:        
        #Select n% as validation
        msk = np.random.rand(len(data)) < 1-(float(DeepForest_config["validation_percent"])/100)
        training = tile_data[msk]
        evaluation=tile_data[~msk]     
        
    else:
        #Select one validation tile
        eval_tile = data.tile.unique()[1]
        evaluation = data[data["tile"] == eval_tile]
        training = data[~(data["tile"] == eval_tile)]
        
        #Log 
        if not experiment==None:
            experiment.log_parameter(eval_tile,"Evaluation Tile")
            
        #Training samples
        if not DeepForest_config["training_images"]=="All":
            num_training_images = DeepForest_config["training_images"]
        
            #Optional shuffle
            if DeepForest_config["shuffle_training"]:
                training.sample(frac=1)
                
            #Select subset of training windows
            training=training.iloc[0:num_training_images]
        
        #Ensure training is sorted by image
        training.sort_values(by="tile")            
        
        #evaluation samples
        if not DeepForest_config["evaluation_images"]=="All":
            num_evaluation_images = DeepForest_config["evaluation_images"]
        
            #Optional shuffle
            if DeepForest_config["shuffle_evaluation"]:
                evaluation.sample(frac=1)
                
            #Select subset of evaluation windows
            evaluation=evaluation.iloc[0:num_evaluation_images]
    
    #Write training to file to view 
    return([training, evaluation])
    
def NEON_annotations(site,DeepForest_config):
   
    glob_path=os.path.join("data",site,"annotations") + "/" + site + "*.xml"
    xmls=glob.glob(glob_path)
    
    annotations=[]
    for xml in xmls:
        r=load_xml(xml,DeepForest_config["rgb_res"])
        annotations.append(r)

    data=pd.concat(annotations)
    
    #Compute list of sliding windows, assumed that all objects are the same extent and resolution
    image_path=os.path.join("data",site, data.rgb_path.unique()[0])
    windows=compute_windows(image=image_path, pixels=DeepForest_config["patch_size"], overlap=DeepForest_config["patch_overlap"])
    
    #Compute Windows
    #Create dictionary of windows for each image
    tile_windows={}
    
    all_images=list(data.rgb_path.unique())

    tile_windows["image"]=all_images
    tile_windows["windows"]=np.arange(0,len(windows))
    
    #Expand grid
    tile_data=expand_grid(tile_windows)    
    
    return [data,tile_data]

def create_windows(data,DeepForest_config):
    """
    Generate windows for a specific tile
    """
    
    #Compute list of sliding windows, assumed that all objects are the same extent and resolution
    base_dir=DeepForest_config["evaluation_tile_dir"]
    image_path=os.path.join(base_dir, data.rgb_path.unique()[0])
    
    windows=compute_windows(image=image_path, pixels=DeepForest_config["patch_size"], overlap=DeepForest_config["patch_overlap"])
    
    #if none
    if windows is None:
        return None
    
    #Compute Windows
    #Create dictionary of windows for each image
    tile_windows={}
    
    all_images=list(data.rgb_path.unique())

    tile_windows["image"]=all_images
    tile_windows["windows"]=np.arange(0,len(windows))
    
    #Expand grid
    tile_data=expand_grid(tile_windows)    
    
    return(tile_data)