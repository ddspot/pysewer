# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

import os
import warnings
from dataclasses import dataclass, field

import geopandas as gpd
import networkx as nx
import numpy as np
import rasterio as rio
import shapely
from pyproj.crs import CRS
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.ops import nearest_points
from sklearn.cluster import AgglomerativeClustering

from .config.manager import get_config
from .helper import (
    ckdnearest,
    get_closest_edge_multiple,
    get_edge_gdf,
    get_node_gdf,
    get_node_keys,
    get_path_distance,
    is_valid_geometry,
    remove_third_dimension,
)
from .optimization import needs_pump
from .simplify import simplify_graph

# Ignore the specific RuntimeWarning
warnings.filterwarnings("ignore", category=RuntimeWarning)


@dataclass
class DEM:
    """
    A class for handling digital elevation model (DEM) data.

    Attributes
    ----------
    file_path : str, optional
        The file path to the DEM raster file.
    raster : rasterio.DatasetReader, optional
        The rasterio dataset reader object for the DEM raster file.
    no_dem : bool
        A flag indicating whether or not a DEM has been loaded.

    Methods
    -------
    get_elevation(point: shapely.geometry.Point) -> int:
        Returns elevation data in meters for a given point rounded to two decimals.
    get_profile(line: shapely.geometry.LineString, dx: int = 10) -> List[Tuple[float, int]]:
        Extracts elevation data from a digital elevation model (DEM) along a given path.
    reproject_dem(crs: CRS) -> None:
        Reprojects the DEM raster to the given CRS.

    Properties
    ----------
    get_crs : CRS
        Returns the coordinate system of the DEM object.
    """

    file_path: str | None = None
    raster: rio.DatasetReader = field(init=False, default=None)
    no_dem: bool = field(init=False, default=True)

    def __post_init__(self):
        if self.file_path:
            self.raster = rio.open(self.file_path)
            self.no_dem = False
            self.remove_nodata(
                fill_value=0
            )  # Call remove_nodata with a default fill_value

    def remove_nodata(self, fill_value: float = 0):
        """
        Removes or replaces nodata values in the DEM raster.

        Parameters
        ----------
        fill_value : float, optional
            The value to replace nodata values with. Default is 0.

        Raises
        ------
        ValueError
            If no DEM is loaded, cannot remove nodata values.
        """
        if self.no_dem:
            raise ValueError("No DEM loaded, cannot remove nodata values")

        with rio.open(self.file_path) as src:
            data = src.read(1)
            nodata = src.nodata

            if nodata is not None:
                data[data == nodata] = fill_value

                kwargs = src.meta.copy()
                kwargs.update(
                    {
                        "nodata": fill_value,
                    }
                )

                with rio.open(self.file_path, "w", **kwargs) as dst:
                    dst.write(data, 1)

        # Reload the raster
        self.raster = rio.open(self.file_path)

    def get_elevation(self, point: shapely.geometry.Point):
        """
        Returns elevation data in meters for a given point rounded to two decimals.

        Parameters
        ----------
        point : shapely.geometry.Point
            The point for which to retrieve elevation data.

        Returns
        -------
        int
            The elevation in meters.

        Raises
        ------
        ValueError
            If the query point is out of bounds or if there is no elevation data for the given coordinates.
        """
        if self.no_dem:
            return 0

        elevation = next(iter(self.raster.sample([(point.x, point.y)])))[0]
        elevation = round(float(elevation), 2)
        if elevation == self.raster.nodata:
            raise ValueError(
                f"No Elevation Data for Coordinates {point.x} {point.y} "
            )
        return elevation

    def get_profile(
        self,
        line: shapely.geometry.LineString,
        dx: int | None = None,
    ):
        """
        Extracts elevation data from a digital elevation model (DEM) along a given path.

        Parameters
        ----------
        line : shapely.geometry.LineString
            The path along which to extract elevation data.
        dx : float, optional
            The sampling resolution in meters. Default is 10.

        Returns
        -------
        List of Tuples
            A list of (x, elevation) tuples representing the x-coordinate and elevation data of the profile.
            The x-coordinate values start at 0 and are spaced at intervals of dx meters.
        """
        config = get_config()
        dx = config.preprocessing.dx if dx is None else dx

        x = np.arange(0, line.length, dx)
        x = np.append(x, line.length)
        interpolated_points = [line.interpolate(dist) for dist in x]
        elevation = [self.get_elevation(ip) for ip in interpolated_points]
        return list(zip(x, elevation, strict=False))

    @property
    def get_crs(self) -> CRS | None:
        """Returns the coordinate system of the DEM Object"""
        if self.no_dem:
            return None
        return self.raster.crs

    # add method to reproject the raster to a given crs in case there is a mismatch between the crs of the raster and the crs of the other input data
    def reproject_dem(self, crs: CRS):
        """
        Reprojects the DEM raster to the given CRS.

        Parameters
        ----------
        crs : CRS
            The target CRS to reproject the raster to.

        Raises
        ------
        ValueError
            If no DEM is loaded, cannot reproject DEM.
        """
        if self.no_dem:
            raise ValueError("No DEM loaded, cannot reproject DEM")

        with rio.open(self.file_path) as src:
            transform, width, height = rio.warp.calculate_default_transform(
                src.crs, crs, src.width, src.height, *src.bounds
            )
            kwargs = src.meta.copy()
            kwargs.update(
                {
                    "crs": crs,
                    "transform": transform,
                    "width": width,
                    "height": height,
                    "nodata": 0,
                }
            )

            # create a new file or overwrite the exisiting DEM with reprojected crs
            with rio.open(self.file_path, "w", **kwargs) as dst:
                for i in range(1, src.count + 1):
                    rio.warp.reproject(
                        source=rio.band(src, i),
                        destination=rio.band(dst, i),
                        src_transform=src.transform,
                        src_crs=src.crs,
                        dst_transform=transform,
                        dst_crs=crs,
                        resampling=rio.warp.Resampling.nearest,
                    )

        # reload the raster
        self.raster = rio.open(self.file_path)


class Roads:
    """
    A class to represent road data from either a shapefile or a geopandas dataframe.
    Attributes:
    ----------
    gdf : geopandas.GeoDataFrame
        A geopandas dataframe containing road data.
    merged_roads : shapely.geometry.MultiLineString
        A shapely MultiLineString object representing the merged road data.
    Methods:
    -------
    __init__(self, input_data: str or geopandas.GeoDataFrame) -> None
        Initializes a Roads object with road data from either a shapefile or a geopandas dataframe.
    """

    def __init__(self, input_data: str | gpd.GeoDataFrame):
        """
        Initializes a Roads object with road data from either a shapefile or a geopandas dataframe.
        Parameters
        ----------
        input_data : str or geopandas.GeoDataFrame
            Path to shapefile or geopandas dataframe containing road data.
        """
        if isinstance(input_data, (str, os.PathLike)):
            self.gdf = gpd.read_file(input_data)
        else:
            self.gdf = input_data

        # drop any rows with missing geometry data
        self.gdf = self.gdf.dropna(subset=["geometry"])

        # drop any rows with geometry being None
        self.gdf = self.gdf[self.gdf["geometry"].notnull()]

        # remove the third dimension from the geometry if present
        self.gdf["geometry"] = [remove_third_dimension(g) for g in self.gdf["geometry"]]
        self.merged_roads = self.gdf.union_all()

    def get_nearest_point(self, point):
        """
        Returns the nearest location to the input point on the street network.
        Parameters
        ----------
        point : shapely.geometry.Point
            Point to find nearest location to.
        Returns
        -------
        shapely.geometry.Point
            Nearest location to the input point on the street network.
        """
        return nearest_points(self.get_merged_roads(), point)[0]

    def get_gdf(self):
        """
        Returns the road data as a geopandas dataframe.
        Returns
        -------
        geopandas.GeoDataFrame
            The road data as a geopandas dataframe.
        """
        return self.gdf

    def get_crs(self):
        """
        Gets the coordinate reference system (CRS) of the Roads object.

        Returns
        -------
        dict
            The coordinate system of the Roads object.
        """
        return self.gdf.crs

    # set the crs using epsd code of the dem
    def set_crs(self, epsg: int):
        self.gdf.crs = CRS.from_epsg(epsg)

    def get_merged_roads(self):
        """
        Merge the road network as a shapely MultiLineString.

        Returns
        -------
        shapely MultiLineString
            merged road network as a shapely MultiLineString
        """
        return self.merged_roads


class Buildings:
    """
    A class to preprocess building data.

    Parameters
    ----------
    input_data : str or geopandas.GeoDataFrame
        Path to a shapefile or a GeoDataFrame containing the input data.
    roads_obj : pysewer.Roads
        A Roads object containing the road network data.

    Attributes
    ----------
    gdf : geopandas.GeoDataFrame
        The input building data.
    roads_obj : pysewer.Roads
        The road network data.

    Methods
    -------
    get_gdf():
        Returns building data as geopandas dataframe.
    get_crs():
        Returns the Coordinate System of the DEM Object.
    cluster_centers(max_connection_length):
        Returns a list of cluster centers for all buildings with greater than max_connection_length distance to the nearest street.

    """

    def __init__(self, input_data: str | gpd.GeoDataFrame, roads_obj: Roads):
        """
        Initialize a Buildings object with building data from either a shapefile or a geopandas dataframe.
        """
        # Digest and clean input Data
        # Allow input to be either path to shp file or gdf
        if isinstance(input_data, (str, os.PathLike)):
            self.gdf = gpd.read_file(input_data)
        else:
            self.gdf = input_data

        # drop any rows with missing geometry data
        self.gdf = self.gdf.dropna(subset=["geometry"])

        # drop any rows with geometry being None
        self.gdf = self.gdf[self.gdf["geometry"].notnull()]

        # preserve the crs of the input data
        original_crs = self.gdf.crs

        # remove the third dimension from the geometry if present
        self.gdf["geometry"] = [remove_third_dimension(g) for g in self.gdf["geometry"]]

        # Convert to Point if Shapefile has MultiPoint Data
        if "MultiPoint" in self.gdf.geometry.type.unique():
            def convert(MultiP):
                return (
                            Point(MultiP.geoms[0].x, MultiP.geoms[0].y)
                            if MultiP.geom_type == "MultiPoint"
                            else MultiP
                        )
            self.gdf["geometry"] = self.gdf["geometry"].apply(convert)

        # apply the conversion function to each geometry in the gdf
        self.gdf["geometry"] = self.gdf["geometry"].apply(self.convert_to_points)

        # flatten the list of points into individual rows
        self.gdf = self.gdf.explode("geometry").reset_index(drop=True)

        # Ensure that the GeoDataFrame is valid, the geometry column is set and reassign the crs
        if "geometry" in self.gdf.columns and not self.gdf.empty:
            self.gdf = gpd.GeoDataFrame(self.gdf, geometry="geometry", crs=original_crs)
        else:
            print("Error: GeoDataFrame is not valid after processing.")

        # ensure that crs is set
        if self.gdf.crs is None:
            self.gdf.crs = original_crs

        self.roads_obj = roads_obj

    def get_gdf(self):
        """
        Returns building data as geopandas dataframe.

        Returns
        -------
        geopandas.GeoDataFrame
            The building data.

        """
        return self.gdf

    def get_crs(self):
        """
        Returns the Coordinate System of the DEM Object.

        Returns
        -------
        dict
            The Coordinate Reference System (CRS) of the building data.

        """
        return self.gdf.crs

    # set the crs using epsd code of the dem to buildng
    def set_crs(self, epsg: int):
        self.gdf.crs = CRS.from_epsg(epsg)

    # add method to convert the polygons or multipolygons to points
    @staticmethod
    def convert_to_points(geometry):
        """
        Converts the building polygons or multipolygons to points.
        """
        if isinstance(geometry, MultiPolygon):
            points = [
                polygon.centroid
                for polygon in geometry.geoms
                if polygon.is_valid and not polygon.is_empty
            ]
        elif isinstance(geometry, Polygon):
            points = (
                [geometry.centroid]
                if geometry.is_valid and not geometry.is_empty
                else []
            )
        elif isinstance(geometry, Point):
            points = [geometry]
        else:
            points = []

        return points

    def cluster_centers(self, max_connection_length: float):
        """
        Returns a list of cluster centers for all buildings with greater than max_connection_length distance to the nearest street.

        Parameters
        ----------
        max_connection_length : float
            The maximum distance between a building and the nearest street for it to be included in the cluster centers list.

        Returns
        -------
        geopandas.GeoDataFrame
            A GeoDataFrame containing the cluster centers and their distances to the nearest street, sorted by distance.

        """
        ####Clustering####
        # get nearest point to street for each building
        # object/float columns built in one go — element-wise .loc writes into
        # ""-initialized columns are rejected by pandas>=3 string dtype
        self.gdf["nearest_point"] = gpd.GeoSeries(
            [self.roads_obj.get_nearest_point(g) for g in self.gdf.geometry],
            index=self.gdf.index,
        )
        self.gdf["distance"] = [
            g.distance(p) for g, p in zip(self.gdf.geometry, self.gdf["nearest_point"], strict=False)
        ]
        # -1 marks "no cluster"; clustering labels are >= 0
        self.gdf["cluster"] = -1
        c_buildings_coords = []
        for b in self.gdf.loc[
            self.gdf["distance"] > max_connection_length
        ].geometry:  # for first feature/row
            coords = np.dstack(b.coords.xy).tolist()[0][0]
            c_buildings_coords.append(coords)
            # Run Scipy Clustering
        try:
            clustering = AgglomerativeClustering(
                n_clusters=None, distance_threshold=max_connection_length
            ).fit(c_buildings_coords)
        except:
            return gpd.GeoDataFrame(geometry=[])
        self.gdf.loc[self.gdf["distance"] >= max_connection_length, "cluster"] = (
            clustering.labels_
        )  # Find Centroid of Clusters
        cluster_centers = []
        for cluster_id in clustering.labels_:
            building_points = self.gdf.loc[
                self.gdf["cluster"] == cluster_id
            ].geometry.tolist()
            if len(building_points) == 2:
                P = LineString(building_points)
            if len(building_points) > 2:
                P = Polygon(building_points)
            if "P" in locals():
                cluster_centers.append(
                    P.centroid
                )  # sort by distance and connect from close to far away to keep flow direction
        cluster_centers = gpd.GeoDataFrame(geometry=cluster_centers)
        merged_roads = self.roads_obj.get_merged_roads()
        cluster_centers["distance"] = [
            g.distance(merged_roads) for g in cluster_centers.geometry
        ]
        cluster_centers.sort_values(by="distance", inplace=True)
        return cluster_centers


class ModelDomain:
    """
    Class for preprocessing input data for the sewer network.

    Parameters
    ----------
    dem : str
        Path to the digital elevation model file.
    roads : str
        Path to the roads shapefile.
    buildings : str
        Path to the buildings shapefile.
    clustering : str, optional
        Clustering method for connecting buildings to the sewer network. Default is "centers".
    pump_penalty : int, optional
        Penalty for adding a pump to the sewer network. Default is 1000.
    connect_buildings : bool, optional
        Whether to connect buildings to the sewer network. Default is True.

    Notes
    -----
    ``clustering`` and ``connect_buildings`` are consumed here, during
    construction (the connection graph is built in ``__init__``). If they
    come from a custom config, call ``pysewer.set_custom_config(...)``
    **before** creating the ModelDomain — overrides applied afterwards
    cannot affect an already-built graph. ``pump_penalty`` is exempt: it is
    resolved from the current config when the connection graph is
    generated, so later config changes (or ``set_pump_penalty``) do apply.

    Attributes
    ----------
    roads : Roads
        Roads object.
    buildings : Buildings
        Buildings object.
    dem : DEM
        DEM object.
    connection_graph : nx.Graph
        Graph representing the road network.
    pump_penalty : int
        Penalty for adding a pump to the sewer network.
    """

    def __init__(
        self,
        dem: str,
        roads: str,
        buildings: str,
        clustering: str | None = None,
        pump_penalty: int | None = None,
        connect_buildings: bool | None = None,
    ):
        """Initialize Model Domain using the input data."""
        config = get_config()
        clustering = config.preprocessing.clustering if clustering is None else clustering
        connect_buildings = (
            config.preprocessing.connect_buildings
            if connect_buildings is None
            else connect_buildings
        )

        self.roads = Roads(roads)
        self.buildings = Buildings(buildings, roads_obj=self.roads)
        self.dem = DEM(dem)

        # improve handling of the crs of the input data.
        # assume that the dem is provided, then use the crs of the dem to reproject the other input data
        # get crs of dem (rasterio.DatasetReader object)
        dem_epsg = self.dem.get_crs.to_epsg()
        # set the roads and buildings to the crs of the dem
        if self.roads.get_crs().to_epsg() is None:
            self.roads.set_crs(dem_epsg)

        if self.buildings.get_crs().to_epsg() is None:
            self.buildings.set_crs(dem_epsg)

        # print(self.roads.get_crs())
        # print(self.buildings.get_crs())
        # print(self.dem.get_crs)

        # Check for coordinate system
        assert (
            self.roads.get_crs().to_epsg() == self.buildings.get_crs().to_epsg()
            and self.dem.get_crs == self.roads.get_crs().to_epsg()
        ), "CRS of input Data does not match"
        assert self.roads.get_crs().is_projected

        # create unsilplified graph
        self.connection_graph = self.create_unsimplified_graph(self.roads.get_gdf())

        self.connection_graph = nx.Graph(self.connection_graph)

        # connecting subgraphs if there are any
        sub_graphs = list(
            
                self.connection_graph.subgraph(c).copy()
                for c in nx.connected_components(self.connection_graph)
            
        )
        if len(sub_graphs) > 1:
            print("connecting subgraphs")
            self.connect_subgraphs()

        # set pump penalty (None → resolved lazily from the current config)
        self.pump_penalty = pump_penalty

        # set node attributes
        nx.set_node_attributes(self.connection_graph, True, "road_network")
        nx.set_node_attributes(self.connection_graph, "", "node_type")

        # check connectivity of G
        self.sewer_graph = nx.DiGraph()

        # connect buildings
        if connect_buildings:
            self.connect_buildings(clustering=clustering)

    @property
    def pump_penalty(self):
        """
        Pump penalty used to weight pump-requiring edges during routing.

        Resolved from the current config at access time unless explicitly
        set (constructor argument or ``set_pump_penalty``), so config
        overrides applied after the ModelDomain was created still take
        effect in ``generate_connection_graph``.
        """
        if self._pump_penalty is None:
            return get_config().preprocessing.pump_penalty
        return self._pump_penalty

    @pump_penalty.setter
    def pump_penalty(self, value):
        self._pump_penalty = value

    def create_unsimplified_graph(self, roads_gdf: gpd.GeoDataFrame) -> nx.Graph:
        """
        Create an unsimplified graph from a GeoDataFrame of roads.
        Parameters
        ----------
        roads_gdf : gpd.GeoDataFrame
            A GeoDataFrame containing road data.
        Returns
        -------
        nx.Graph
            An unsimplified graph containing nodes and edges from the GeoDataFrame.
        """
        # Initialize an empty undirected graph
        G_unsimplified = nx.Graph()

        # validate the the geometry column, so that it contains only Linestring or MultiLineString geometries
        if (
            not roads_gdf["geometry"]
            .apply(lambda geom: isinstance(geom, (LineString, MultiLineString)))
            .all()
        ):
            raise ValueError(
                "Invalid geometry type. Only LineString or MultiLineString geometries are allowed."
            )

        # iterate over each row in the GeoDataFrame
        for _index, row in roads_gdf.iterrows():
            geometry = row["geometry"]
            road_attrs = row.drop(
                "geometry"
            ).to_dict()  # Convert other attributes to a dictionary

            # Handle MultiLineString geometries
            if isinstance(geometry, MultiLineString):
                # iterate over each LineString in the lines list
                for line in geometry.geoms:
                    for i in range(len(line.coords) - 1):
                        start_point = line.coords[i]
                        end_point = line.coords[i + 1]

                        # Create a LineString geometry for the segment
                        segment_geometry = LineString([start_point, end_point])

                        # Add edge to the graph, include the segment geometry
                        G_unsimplified.add_edge(
                            start_point,
                            end_point,
                            geometry=segment_geometry,
                            **road_attrs,
                        )
            else:  # Single LineString geometry
                for i in range(len(geometry.coords) - 1):
                    start_point = geometry.coords[i]
                    end_point = geometry.coords[i + 1]

                    # Create a LineString geometry for the segment
                    segment_geometry = LineString([start_point, end_point])

                    # Add edge to the graph, include the segment geometry
                    G_unsimplified.add_edge(
                        start_point, end_point, geometry=segment_geometry, **road_attrs
                    )

        return G_unsimplified

    def connect_buildings(
        self,
        max_connection_length: int | None = None,
        clustering: str | None = None,
    ):
        """
        Connects the buildings in the network by adding nodes to the graph.
        Parameters
        ----------
        max_connection_length : int, optional
            The maximum distance between a building and the nearest street for it to be included in the cluster
            centers list. The default is 30.
        clustering : str, optional
            The method used to cluster the buildings. Can be "centers" or "none". The default is "centers".
        Returns
        -------
        None
        Notes
        -----
        This method adds nodes to the graph to connect the buildings in the network. It first gets the building points
        and then clusters them based on the specified method. If clustering is set to "centers", it gets the cluster
        centers and finds the closest edges to them. It then adds nodes to the graph for each cluster center, with the
        closest edge as an attribute. If clustering is set to "none", it simply adds nodes to the graph for each building.
        In both cases, it finds the closest edges to the buildings and adds nodes to the graph for each building, with
        the closest edge as an attribute.
        Examples
        --------
        >>> network = Network()
        >>> network.connect_buildings(max_connection_length=50, clustering="centers")
        """
        config = get_config()
        max_connection_length = (
            config.preprocessing.max_connection_length
            if max_connection_length is None
            else max_connection_length
        )
        clustering = (
            config.preprocessing.clustering if clustering is None else clustering
        )

        # get building points:
        building_gdf = self.buildings.get_gdf()

        # validate the the geometry column, so that it contains only Point geometries
        if (
            not building_gdf["geometry"]
            .apply(lambda geom: isinstance(geom, Point))
            .all()
        ):
            raise ValueError(
                "Invalid geometry type. Only Point geometries are allowed."
            )

        building_points = [
            geom for geom in building_gdf.geometry if is_valid_geometry(geom)
        ]

        if clustering == "centers":
            cluster_centers_gdf = self.buildings.cluster_centers(max_connection_length)
            cluster_centers = [
                geom for geom in cluster_centers_gdf.geometry if is_valid_geometry(geom)
            ]

            if len(cluster_centers) > 0:
                # check if cluster centers or conected graph is empty or contains invalid values
                if (
                    not cluster_centers
                    or not self.connection_graph
                    or any(np.isnan(x.x) or np.isnan(x.y) for x in cluster_centers)
                ):
                    warnings.warn(
                        "No cluster centers found or invalid values in cluster centers", stacklevel=2
                    )

                # closest edges to cluster centers
                closest_edges_cc = get_closest_edge_multiple(
                    self.connection_graph, cluster_centers
                )
                for k, center in enumerate(cluster_centers):
                    try:
                        self.add_node(center, "cluster_center", closest_edges_cc[k])
                    except Exception as e:
                        print(f"Error adding cluster center {center}: {e}")
                        self.add_node(center, "cluster_center")

            # closest edges to buildings
            closest_edges_b = get_closest_edge_multiple(
                self.connection_graph, building_points
            )
            for _, i in building_gdf.iterrows():
                try:
                    self.add_node(
                        i.geometry, "building", closest_edges_b[i], i.to_dict()
                    )
                except:
                    self.add_node(i.geometry, "building", node_attributes=i.to_dict())

        else:
            # directly add each building to the graph
            for _, i in building_gdf.iterrows():
                if is_valid_geometry(i.geometry):
                    self.add_node(i.geometry, "building", node_attributes=i.to_dict())
                else:
                    print(f"Invalid geometry for building {i}")

    def add_node(self, point, node_type, closest_edge=None, node_attributes=None):
        """
        Adds a node to the connection graph.

        Parameters
        ----------
        point : shapely.geometry.Point
            The point to add as a node.
        node_type : str
            The type of node to add.
        closest_edge : shapely.geometry.LineString, optional
            The closest edge to the point. Defaults to None.
        node_attributes : dict, optional
            Additional attributes to add to the node. Defaults to None.

        Returns
        -------
        None

        Notes
        -----
        This method adds a node to the connection graph. If `closest_edge` is not provided, it finds the closest edge to
        the point and uses that as the `closest_edge`. It then disconnects edges from the node and adds the node to the
        connection graph. If there are any cluster centers, it connects the node to the nearest cluster center. If there
        are no cluster centers, it connects the node to the road network.

        """
        # when called for single nodes, get closest edge:
        if closest_edge is None:
            edge_gdf = get_edge_gdf(self.connection_graph)
            edge_gdf["closest_edge"] = edge_gdf.geometry.to_list()
            closest_edge = ckdnearest(
                gpd.GeoDataFrame(geometry=[point]), edge_gdf, ["closest_edge"]
            ).iloc[0, 1]
        conn_point = nearest_points(closest_edge, point)[0]
        # disconnect edges from node
        cluster_nodes = get_node_keys(
            self.connection_graph, field="node_type", value="cluster_center"
        )
        if node_attributes is None:
            self.connection_graph.add_node((point.x, point.y), node_type=node_type)
        else:
            self.connection_graph.add_node(
                (point.x, point.y), node_type=node_type, **node_attributes
            )

        if len(cluster_nodes) > 0:
            x, y = zip(*cluster_nodes, strict=False)
            cluster_centroids_gdf = gpd.GeoDataFrame(geometry=gpd.points_from_xy(x, y))
            next_cluster_center = nearest_points(
                cluster_centroids_gdf.union_all(), point
            )[0]

            if conn_point.distance(point) < next_cluster_center.distance(point):
                self.connect_to_roadnetwork(
                    self.connection_graph, point, conn_point, closest_edge
                )
            else:
                self.connection_graph.add_edge(
                    (point.x, point.y), (next_cluster_center.x, next_cluster_center.y)
                )

        else:
            self.connect_to_roadnetwork(
                self.connection_graph, point, conn_point, closest_edge
            )

    def connect_to_roadnetwork(
        self,
        G: nx.Graph,
        new_node,
        conn_point,
        closest_edge,
        add_private_sewer: bool | None = None,
    ):
        """
        Connects a new node to the road network by inserting a connection point on the closest edge and adjusting edges.

        Args:
            G (networkx.Graph): The road network graph.
            new_node (NodeTpye): The new node to be connected to the road network.
            conn_point (pysewer.Point): The point where the new node will be connected to the road network.
            closest_edge (pysewer.Edge): The closest edge to the new node.
            add_private_sewer (bool, optional): Whether to add a private sewer between the new node and the connection point. Defaults to True.

        Returns:
            bool: True if the connection was successful, False otherwise.
        """
        config = get_config()
        add_private_sewer = (
            config.preprocessing.add_private_sewer
            if add_private_sewer is None
            else add_private_sewer
        )

        u = closest_edge.coords[0]
        v = closest_edge.coords[1]
        # inserts new connection point on a edge and adjusts edges
        # Find Edge intersecting conn_point

        G.remove_edge(u, v)

        G.add_node((conn_point.x, conn_point.y), connection_node=True)

        # add edges
        G.add_edge(u, (conn_point.x, conn_point.y))
        G.add_edge((conn_point.x, conn_point.y), v)
        if add_private_sewer:
            G.add_edge(
                (new_node.x, new_node.y),
                (conn_point.x, conn_point.y),
                private_sewer=True,
            )
        return True

    def generate_connection_graph(self) -> nx.MultiDiGraph:
        """
        Generates a connection graph from the given connection data and returns it.
        This method simplifies the connection graph, removes any self loops, sets trench depth node attributes to 0,
        calculates the geometry, distance, profile, needs_pump, weight, and elevation attributes for each edge and node
        in the connection graph.

        Returns:
        --------
        nx.MultiDiGraph
            A directed graph representing the connections between the different points in the network.
        """
        print(f"\nGenerating connection graph with pump penalty: {self.pump_penalty}")
        config = get_config()
        simplified_graph = simplify_graph(self.connection_graph)
        self.junction_graph = simplified_graph
        connection_digraph = nx.MultiDiGraph(simplified_graph)
        # remove any self loops
        connection_digraph.remove_edges_from(
            list(nx.selfloop_edges(connection_digraph))
        )
        nx.set_node_attributes(connection_digraph, 0, name="trench_depth")

        # Count edges that need pumps
        edges_needing_pumps = 0
        total_edges = 0

        for u, v, _a in connection_digraph.edges(data=True):
            # ensure that edge exists before accessing its attributes
            if connection_digraph.has_edge(u, v):
                total_edges += 1
                detailed_path = nx.shortest_path(self.connection_graph, u, v)
                connection_digraph[u][v][0]["geometry"] = LineString(detailed_path)
                dist = get_path_distance(detailed_path)
                connection_digraph[u][v][0]["distance"] = dist
                connection_digraph[u][v][0]["profile"] = self.dem.get_profile(
                    LineString(detailed_path)
                )
                # checking if the profile attribute exist before using it
                if "profile" in connection_digraph[u][v][0]:
                    # Check if a pump is needed
                    needs_pump_flag, height, trench_depth = needs_pump(
                        profile=connection_digraph[u][v][0]["profile"],
                        min_slope=config.optimization.min_slope,
                        tmax=config.optimization.tmax,
                        tmin=config.optimization.tmin,
                        inflow_trench_depth=config.optimization.inflow_trench_depth,
                    )
                    profile = connection_digraph[u][v][0]["profile"]
                    if profile and profile[-1][0] != profile[0][0]:
                        slope = (profile[-1][1] - profile[0][1]) / (
                            profile[-1][0] - profile[0][0]
                        )
                    else:
                        slope = 0
                    connection_digraph[u][v][0]["needs_pump"] = needs_pump_flag
                    connection_digraph[u][v][0]["height"] = height
                    connection_digraph[u][v][0]["trench_depth"] = trench_depth
                    connection_digraph[u][v][0]["slope"] = slope

                    # Constraint flags
                    cover_profile = [
                        topo[1] - td[1] for td, topo in zip(trench_depth, profile, strict=False)
                    ]
                    min_cover = min(cover_profile) if cover_profile else 0
                    violations = []
                    # A pump does not remedy insufficient cover (deeper
                    # trenching does, governed by tmin), so a min_cover
                    # violation is recorded but does not force needs_pump.
                    if min_cover < config.optimization.min_cover:
                        violations.append("min_cover")
                    if dist < config.optimization.min_pipe_length:
                        violations.append("min_pipe_length")
                    if abs(slope) > abs(config.optimization.max_slope):
                        violations.append("max_slope")

                    connection_digraph[u][v][0]["constraint_violations"] = violations

                    if connection_digraph[u][v][0]["needs_pump"]:
                        edges_needing_pumps += 1
                        connection_digraph[u][v][0]["weight"] = dist * self.pump_penalty
                        print(
                            f"Edge {u}->{v} needs pump. Distance: {dist:.2f}m, Weight: {dist * self.pump_penalty:.2f}"
                        )
                    else:
                        connection_digraph[u][v][0]["weight"] = dist
                        print(
                            f"Edge {u}->{v} gravity flow. Distance: {dist:.2f}m, Weight: {dist:.2f}"
                        )

        print("\nSummary:")
        print(f"Total edges: {total_edges}")
        print(f"Edges needing pumps: {edges_needing_pumps}")
        print(
            f"Percentage of edges needing pumps: {(edges_needing_pumps/total_edges)*100:.1f}%"
        )

        for n in connection_digraph.nodes():
            attr = {n: {"elevation": self.dem.get_elevation(Point(n))}}
            nx.set_node_attributes(connection_digraph, attr)
        return connection_digraph

    def add_sink(self, sink_location: tuple, label: str = "wwtp"):
        """
        Adds a sink node to the graph at the specified location.

        Parameters
        ----------
        sink_location : tuple
            A tuple containing the x and y coordinates of the sink location.

        Returns
        -------
        None
        """
        self.add_node(Point(sink_location), label)

    def reset_sinks(self):
        """
        Resets the sinks in the connection graph by setting their node_type attribute to an empty string.
        Returns
        -------
        None
            This method does not return anything.
        """
        sinks = self.get_sinks()
        if len(sinks) > 0:
            for s in sinks:
                node_attrs = {s: {"node_type": ""}}
            nx.set_node_attributes(self.connection_graph, node_attrs)

    def set_sink_lowest(self, candidate_nodes: list | None = None):
        """
        Sets the sink node to the lowest point in the graph.

        Parameters
        ----------
        candidate_nodes : list, optional
            A list of candidate nodes to consider for the sink node. If None, all nodes except buildings are considered.
        Returns
        -------
        None

        Notes
        -----
        This method sets the sink node to the lowest point in the graph. If candidate_nodes is not None, only the nodes in candidate_nodes that are not buildings are considered.
        """

        r = {}
        buildings = self.get_buildings()
        if candidate_nodes is None:
            for n in [k for k in self.connection_graph.nodes if k not in buildings]:
                r[self.dem.get_elevation(Point(n))] = n
        else:
            for n in [
                k
                for k in self.connection_graph.nodes
                if k not in buildings
                and candidate_nodes is not None
                and k in candidate_nodes
            ]:
                r[self.dem.get_elevation(Point(n))] = n
        try:
            lowest_node = r[min(r.keys())]
            # shift lowest by a meter to allow connection point and maintain graph topology
            lowest_node = (lowest_node[0] + 1, lowest_node[1])
            self.add_sink(lowest_node)
        except:
            pass

    def get_sinks(self):
        """Returns a list of node keys for all wastewater treatment plants (wwtp) in the connection graph."""
        config = get_config()
        return get_node_keys(
            self.connection_graph,
            field=config.preprocessing.field_get_sinks,
            value=config.preprocessing.value_get_sinks,
        )

    def set_pump_penalty(self, pp):
        """
        Set the pump penalty for the current instance of the ModelDomain class.
        Parameters
        ----------
        pp : float
            The pump penalty to set.
        Returns
        -------
        None
        """
        self.pump_penalty = pp

    def get_buildings(self):
        """Returns a list of node keys for all buildings in the connection graph."""
        config = get_config()
        return get_node_keys(
            self.connection_graph,
            field=config.preprocessing.field_get_buildings,
            value=config.preprocessing.value_get_buildings,
        )

    def connect_subgraphs(self):
        """Identifies unconnected street subnetworks and connects them based on shortest distance"""
        G = self.connection_graph
        sub_graphs = list(G.subgraph(c).copy() for c in nx.connected_components(G))
        while len(sub_graphs) > 1:
            # select one subgraph
            sg = sub_graphs.pop()
            G_without_sg = sub_graphs.pop()

            while len(sub_graphs) > 0:
                G_without_sg = nx.compose(G_without_sg, sub_graphs.pop())

            # get shortest edge between sg and G_withouto_sg:
            sg_gdf = get_node_gdf(sg).union_all()
            G_without_sg_gdf = get_node_gdf(G_without_sg).union_all()

            # # validate the sd_gdf and G_without_sg_gdf are geodataframes
            # if not isinstance(sg_gdf, gpd.GeoDataFrame) or not isinstance(G_without_sg_gdf, gpd.GeoDataFrame):
            #     raise ValueError("Invalid data type. Expected GeoDataFrame.")

            # check if either gdf is empty or contains invalid values
            # check if either gdf is empty or contains invalid values
            if (
                sg_gdf.is_empty or G_without_sg_gdf.is_empty or sg_gdf.isna().any()
                if hasattr(sg_gdf, "isna")
                else (
                    False or G_without_sg_gdf.isna().any()
                    if hasattr(G_without_sg_gdf, "isna")
                    else False
                )
            ):
                warnings.warn(
                    "Skipped an iteration: Empty or invalid subgraph detected...Skipping this connection.", stacklevel=2
                )
                continue
            connection_points = nearest_points(sg_gdf, G_without_sg_gdf)

            # add edge
            G.add_edge(
                (connection_points[0].x, connection_points[0].y),
                (connection_points[1].x, connection_points[1].y),
                road_network=True,
            )
            # get updated subgraph list
            sub_graphs = list(
                G.subgraph(c).copy() for c in nx.connected_components(G)
            )
